# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CadCraft contributors
"""
archicad_bundle.py: write a project bundle (one .zip: the model as IFC, the layouts as PDF + DWG, and manifest.json with
the view map, layout book, property definitions, classifications and standards) from a project open in Archicad.

It talks to Archicad's JSON API (Archicad 24 and later) over http://127.0.0.1:<port>, with the Python standard library
only, so it runs in Archicad's Python palette and from a terminal without installing anything. It never reads .pln /
.tpl files itself and uses no Graphisoft or ODA SDK. Format: FORMAT.md (cadcraft-project-bundle 1).

    python archicad_bundle.py --probe                 # step 1: which API commands this Archicad answers → probe.json
    python archicad_bundle.py --out C:/bundles        # the bundle, published with the publisher set "CadCraft bundle"
    python archicad_bundle.py --publisher-set "My set" --out . --port 19723 --no-publish

Every command that does not answer becomes a line in the manifest's "notes"; nothing is made up.
"""
import argparse
import datetime
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

VERSION = "0.1.0"
FORMAT = "cadcraft-project-bundle"
PORTS = range(19723, 19745)
TIMEOUT = 120


# ---- the JSON API ----

class ApiError(Exception):
    def __init__(self, command, code, message):
        super().__init__("%s: %s %s" % (command, code, message))
        self.command, self.code, self.message = command, code, message


class Archicad:
    """One Archicad instance's JSON API. Records every call for --probe and the manifest's notes."""

    def __init__(self, port=None, host="127.0.0.1"):
        self.host = host
        self.port = port or self.find_port()
        self.calls = []

    def find_port(self):
        for p in PORTS:
            try:
                if self._post(p, {"command": "API.IsAlive"}, timeout=2).get("result", {}).get("isAlive"):
                    return p
            except Exception:
                continue
        raise SystemExit("No Archicad answered on ports %d-%d. Open the project in Archicad first." % (PORTS.start, PORTS.stop - 1))

    def _post(self, port, body, timeout=TIMEOUT):
        req = urllib.request.Request("http://%s:%d" % (self.host, port), data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def call(self, command, parameters=None):
        """API.<command> → its result, or ApiError."""
        body = {"command": command if "." in command else "API." + command}
        if parameters is not None:
            body["parameters"] = parameters
        t0 = time.time()
        try:
            out = self._post(self.port, body)
        except urllib.error.URLError as e:
            self.calls.append({"command": body["command"], "ok": False, "error": "network", "message": str(e), "ms": int((time.time() - t0) * 1000)})
            raise ApiError(body["command"], "network", str(e))
        ms = int((time.time() - t0) * 1000)
        if not out.get("succeeded", False):
            err = out.get("error") or {}
            self.calls.append({"command": body["command"], "ok": False, "error": err.get("code"), "message": err.get("message"), "ms": ms})
            raise ApiError(body["command"], err.get("code"), err.get("message"))
        self.calls.append({"command": body["command"], "ok": True, "ms": ms})
        return out.get("result") or {}

    def tapir(self, name, parameters=None):
        """A Tapir add-on command (optional add-on; github.com/ENZYME-APD/tapir-archicad-automation)."""
        res = self.call("API.ExecuteAddOnCommand", {
            "addOnCommandId": {"commandNamespace": "TapirCommand", "commandName": name},
            "addOnCommandParameters": parameters or {}})
        out = res.get("addOnCommandResponse", res)
        if isinstance(out, dict) and out.get("success") is False:
            raise ApiError("Tapir." + name, "tapir", json.dumps(out.get("error")))
        return out


def safe(fn, notes, what):
    """Run one collector; a missing command becomes a note."""
    try:
        return fn()
    except ApiError as e:
        notes.append("%s: not available from this Archicad (%s %s)." % (what, e.code, e.message or ""))
    except Exception as e:  # a shape we did not expect: keep going, say so
        notes.append("%s: could not be read (%s)." % (what, e))
    return None


def guid(x):
    """{"guid": "..."} or {"xxxId": {"guid": "..."}} → the guid string."""
    if isinstance(x, dict):
        if "guid" in x:
            return x["guid"]
        for v in x.values():
            g = guid(v)
            if g:
                return g
    return None


# ---- collectors (each returns manifest parts; shapes per Graphisoft's JSON API reference, checked by --probe) ----

VIEW_KINDS = {
    "StoryItem": "plan", "SectionItem": "section", "ElevationItem": "elevation", "InteriorElevationItem": "interior_elevation",
    "DetailDrawingItem": "detail", "DetailItem": "detail", "WorksheetItem": "worksheet", "WorksheetDrawingItem": "worksheet",
    "DocumentFrom3DItem": "document_3d", "CameraItem": "3d", "CameraSetItem": "3d", "AxonometryItem": "3d", "PerspectiveItem": "3d",
    "ScheduleItem": "schedule", "ProjectIndexItem": "list", "TextListItem": "list", "GraphicListItem": "list",
}
FOLDERS = {"FolderItem", "UndefinedItem", "ProjectMapRootItem", "ViewMapRootItem", "LayoutBookRootItem", "SubSetItem"}


def tree(ac, tree_type):
    res = ac.call("GetNavigatorItemTree", {"navigatorTreeId": {"type": tree_type}})
    return (res.get("navigatorTree") or {}).get("rootItem") or {}


def walk(node, path, out):
    """Depth-first over navigator items → [(item, folder path)]."""
    for ch in node.get("children") or []:
        item = ch.get("navigatorItem", ch)
        name = ((item.get("prefix") or "") + " " + (item.get("name") or "")).strip()
        out.append((item, path))
        sub = path + [name] if item.get("type") in FOLDERS or item.get("children") else path
        walk(item, sub, out)
    return out


def collect_views(ac, notes, stories):
    root = tree(ac, "ViewMap")
    views = []
    details = {}
    items = [(i, p) for i, p in walk(root, [], []) if i.get("type") not in FOLDERS and i.get("type") != "LayoutItem"]
    ids = [{"navigatorItemId": i.get("navigatorItemId")} for i, _ in items if i.get("navigatorItemId")]
    # View settings (layer combination, scale, model view options) — newer versions only; else names and kinds stay.
    got = safe(lambda: ac.call("GetDetailsOfNavigatorItems", {"navigatorItemIds": ids}), notes, "View settings (layer combination, scale, model view options)")
    if got:
        for d in got.get("navigatorItemDetailsList") or got.get("details") or []:
            d = d.get("details", d)
            g = guid(d.get("navigatorItemId"))
            if g:
                details[g] = d
    for item, path in items:
        g = guid(item.get("navigatorItemId")) or ""
        d = details.get(g, {})
        story = None
        if item.get("type") == "StoryItem" or "floorNumber" in d:
            fl = d.get("floorNumber", item.get("floorNumber"))
            if isinstance(fl, int):
                story = fl
            elif stories:
                nm = item.get("name") or ""
                for s in stories:
                    if s["name"] and s["name"] in nm:
                        story = s["index"]
                        break
        scale = d.get("drawingScale") or d.get("scale")
        if isinstance(scale, (int, float)) and 0 < scale < 1:
            scale = round(1 / scale, 4)
        view = {
            "id": g, "name": ((item.get("prefix") or "") + " " + (item.get("name") or "")).strip() or g,
            "path": [p for p in path if p][:16], "kind": VIEW_KINDS.get(item.get("type"), "other"),
            "story": story, "scale": scale if isinstance(scale, (int, float)) else None,
            "layer_combination": d.get("layerCombination") or d.get("layerCombinationName"),
            "model_view_options": d.get("modelViewOptions") or d.get("modelViewOptionsName"),
            "pen_set": d.get("penSetName") or d.get("penTable"),
            "structure_display": d.get("structureDisplay"),
            "dimension_style": d.get("dimensionStyle"),
            "renovation_filter": d.get("renovationFilter"),
            "graphic_override": d.get("graphicOverrideCombination"),
        }
        views.append({k: v for k, v in view.items() if v not in (None, "", [])})
    return views


def collect_layouts(ac, notes):
    root = tree(ac, "LayoutBook")
    layouts, masters = [], []
    for item, path in walk(root, [], []):
        t = item.get("type")
        g = guid(item.get("navigatorItemId")) or ""
        name = item.get("name") or g
        if t == "LayoutItem":
            drawings = []
            for ch in item.get("children") or []:
                c = ch.get("navigatorItem", ch)
                if c.get("type") == "DrawingItem":
                    drawings.append({"name": c.get("name") or "", "view": guid(c.get("sourceNavigatorItemId"))})
            layouts.append({"id": g, "name": name, "number": item.get("prefix") or None, "path": [p for p in path if p][:16],
                            "drawings": [{k: v for k, v in d.items() if v} for d in drawings]})
        elif t in ("MasterLayoutItem", "MasterItem"):
            masters.append({"id": g, "name": name})
    for l in layouts:
        for k in [k for k, v in l.items() if v in (None, [], "")]:
            del l[k]
    return layouts, masters


def collect_stories(ac, notes):
    res = safe(lambda: ac.tapir("GetStories"), notes, "Stories (Tapir GetStories)")
    out = []
    for i, s in enumerate((res or {}).get("stories") or []):
        out.append({"index": s.get("index", i), "name": s.get("uName") or s.get("name") or "", "elevation": s.get("level", s.get("elevation"))})
    return out


ATTR_KINDS = [("Layer", "GetLayerAttributes"), ("LayerCombination", "GetLayerCombinationAttributes"), ("PenTable", "GetPenTableAttributes"),
              ("Line", "GetLineAttributes"), ("Fill", "GetFillAttributes"), ("Surface", "GetSurfaceAttributes"),
              ("Composite", "GetCompositeAttributes"), ("BuildingMaterial", "GetBuildingMaterialAttributes")]


def color_hex(c):
    if not isinstance(c, dict):
        return None
    try:
        return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(float(c.get(k, 0)) * 255)))) for k in ("red", "green", "blue"))
    except (TypeError, ValueError):
        return None


def collect_standards(ac, notes):
    st = {}
    raw = {}
    for kind, getter in ATTR_KINDS:
        def get(kind=kind, getter=getter):
            ids = ac.call("GetAttributesByType", {"attributeType": kind}).get("attributeIds") or []
            if not ids:
                return []
            return ac.call(getter, {"attributeIds": ids}).get("attributes") or []
        raw[kind] = safe(get, notes, "Attributes: " + kind) or []
    names = {}
    for a in raw["Layer"]:
        a = a.get("layerAttribute", a)
        names[guid(a.get("attributeId"))] = a.get("name")
        st.setdefault("layers", []).append({"name": a.get("name"), "hidden": a.get("isHidden"), "locked": a.get("isLocked"),
                                            "wireframe": a.get("isWireframe"), "group": a.get("intersectionGroupNumber")})
    for a in raw["LayerCombination"]:
        a = a.get("layerCombinationAttribute", a)
        layers = {}
        for l in a.get("layerAttributes") or a.get("layers") or []:
            nm = l.get("name") or names.get(guid(l.get("attributeId")))
            if nm:
                layers[nm] = {"hidden": l.get("isHidden"), "locked": l.get("isLocked"), "wireframe": l.get("isWireframe")}
        st.setdefault("layer_combinations", []).append({"name": a.get("name"), "layers": layers})
    for a in raw["PenTable"]:
        a = a.get("penTableAttribute", a)
        pens = [{"index": p.get("index"), "color": color_hex(p.get("color")), "weight": p.get("width") or p.get("weight")}
                for p in (a.get("pens") or []) if isinstance(p, dict)]
        st.setdefault("pen_sets", []).append({"name": a.get("name"), "pens": pens})
    for a in raw["Line"]:
        a = a.get("lineAttribute", a)
        kind = {"SolidLine": "solid", "DashedLine": "dashed", "SymbolLine": "symbol"}.get(a.get("lineType") or a.get("type"), "solid")
        st.setdefault("line_types", []).append({"name": a.get("name"), "kind": kind})
    for a in raw["Fill"]:
        a = a.get("fillAttribute", a)
        kind = {"VectorFill": "vector", "SymbolFill": "symbol", "SolidFill": "solid", "EmptyFill": "empty", "ImageFill": "image",
                "LinearGradientFill": "gradient", "RadialGradientFill": "gradient"}.get(a.get("subType") or a.get("type"), "other")
        st.setdefault("fills", []).append({"name": a.get("name"), "kind": kind})
    for a in raw["Surface"]:
        a = a.get("surfaceAttribute", a)
        st.setdefault("surfaces", []).append({"name": a.get("name"), "color": color_hex(a.get("surfaceColor") or a.get("color")),
                                              "transparency": a.get("transparency")})
    mat_names = {}
    for a in raw["BuildingMaterial"]:
        a = a.get("buildingMaterialAttribute", a)
        mat_names[guid(a.get("attributeId"))] = a.get("name")
        st.setdefault("materials", []).append({"name": a.get("name"), "fill": None, "surface": None})
    for a in raw["Composite"]:
        a = a.get("compositeAttribute", a)
        skins = []
        for s in a.get("compositeSkins") or a.get("skins") or []:
            skins.append({"material": mat_names.get(guid(s.get("buildingMaterialId"))), "thickness": s.get("thickness"),
                          "core": s.get("isCore")})
        st.setdefault("composites", []).append({"name": a.get("name"), "skins": skins})
    return clean(st)


PTYPE = {"string": "string", "integer": "integer", "number": "number", "length": "length", "area": "area", "volume": "volume",
         "angle": "angle", "boolean": "boolean", "singleEnum": "enum", "multiEnum": "list", "list": "list"}


def collect_properties(ac, notes):
    groups, defs = {}, []
    def detailed():
        ids = ac.call("GetAllPropertyIds", {"propertyType": "UserDefined"}).get("propertyIds") or []
        if not ids:
            return []
        return ac.call("GetDetailsOfProperties", {"properties": ids}).get("propertyDefinitions") or []
    got = safe(detailed, notes, "Property definitions in detail (GetDetailsOfProperties)")
    if got is not None:
        for d in got:
            d = d.get("propertyDefinition", d)
            g = d.get("group") or {}
            gid = guid(g.get("propertyGroupId")) or g.get("name")
            if gid and gid not in groups:
                groups[gid] = {"id": gid, "name": g.get("name") or gid}
            vt = d.get("valueType")
            if d.get("collectionType") in ("singleEnum", "multipleEnum", "multiEnum"):
                vt = "singleEnum" if d.get("collectionType") == "singleEnum" else "multiEnum"
            defs.append({"id": guid(d.get("propertyId")), "group": gid, "name": d.get("name"), "type": PTYPE.get(vt, "string"),
                         "description": d.get("description"),
                         "values": [str((v.get("enumValue") or v).get("displayValue", "")) for v in d.get("possibleEnumValues") or []],
                         "classifications": [guid(c) for c in d.get("availability") or [] if guid(c)]})
    else:
        names = safe(lambda: ac.call("GetAllPropertyNames"), notes, "Property names (GetAllPropertyNames)") or {}
        for p in names.get("properties") or []:
            if p.get("type") != "UserDefined":
                continue
            ln = p.get("localizedName") or []
            if len(ln) >= 2:
                groups.setdefault(ln[0], {"id": ln[0], "name": ln[0]})
                defs.append({"id": ln[0] + "/" + ln[1], "group": ln[0], "name": ln[1]})
    return {"groups": list(groups.values()), "definitions": [clean(d) for d in defs]}


def collect_classifications(ac, notes):
    out = []
    systems = safe(lambda: ac.call("GetAllClassificationSystems").get("classificationSystems") or [], notes, "Classification systems") or []
    def items(lst, depth=0):
        res = []
        for it in lst or []:
            it = it.get("classificationItem", it)
            res.append({"id": guid(it.get("classificationItemId")), "code": it.get("id") or "", "name": it.get("name") or "",
                        "children": items(it.get("children"), depth + 1) if depth < 12 else []})
        return res
    for s in systems:
        sid = s.get("classificationSystemId")
        got = safe(lambda: ac.call("GetAllClassificationsInSystem", {"classificationSystemId": sid}), notes, "Classifications of " + str(s.get("name")))
        out.append({"id": guid(sid), "name": s.get("name"), "version": s.get("version"), "source": s.get("source"),
                    "items": items((got or {}).get("classificationItems"))})
    return out


def clean(x):
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items() if v not in (None, "", [], {})}
    if isinstance(x, list):
        return [clean(v) for v in x]
    return x


# ---- publishing and packing ----

def publish(ac, set_name, folder, notes):
    """Publish the set into `folder` → the files it wrote."""
    before = set()
    ac.call("PublishPublisherSet", {"publisherSetName": set_name, "outputPath": folder})
    found = []
    for root, _, files in os.walk(folder):
        for f in files:
            p = os.path.join(root, f)
            if p not in before:
                found.append(p)
    if not found:
        notes.append("The publisher set \"%s\" wrote no files into %s." % (set_name, folder))
    return found


def slug(s):
    keep = "".join(c if c.isalnum() or c in "-_." else "-" for c in (s or "").strip())
    return keep.strip("-")[:80] or "file"


def pack(manifest, published, out_path, probe=None):
    """manifest + the published files → out_path (.zip). IFC → model/, PDF/DWG → layouts/ (matched to layouts by name)."""
    entries = {}
    ifcs = [p for p in published if p.lower().endswith(".ifc")]
    if ifcs:
        name = "model/" + slug(os.path.basename(ifcs[0]))
        entries[name] = ifcs[0]
        manifest["model"]["file"] = name
        if len(ifcs) > 1:
            manifest["notes"].append("The publisher set wrote %d IFC files; the bundle keeps %s." % (len(ifcs), os.path.basename(ifcs[0])))
    by_stem = {}
    for p in published:
        ext = p.lower().rsplit(".", 1)[-1]
        if ext in ("pdf", "dwg"):
            by_stem.setdefault(os.path.splitext(os.path.basename(p))[0].lower(), {})[ext] = p
    for l in manifest.get("layouts", []):
        keys = [k for k in (l.get("number"), l.get("name"), ((l.get("number") or "") + " " + l.get("name", "")).strip()) if k]
        for k in keys:
            hit = by_stem.pop(k.lower(), None)
            if hit:
                for ext, p in hit.items():
                    name = "layouts/%s.%s" % (slug(l.get("number") or l["name"]), ext)
                    entries[name] = p
                    l[ext] = name
                break
    if by_stem:
        manifest["notes"].append("%d published PDF/DWG files did not match a layout by name and were left out: %s"
                                 % (len(by_stem), ", ".join(sorted(by_stem))[:500]))
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, allowZip64=False) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for name, src in entries.items():
            z.write(src, name, compress_type=zipfile.ZIP_STORED if name.endswith((".pdf", ".dwg")) else zipfile.ZIP_DEFLATED)
        if probe is not None:
            z.writestr("probe.json", json.dumps(probe, indent=2))
    return out_path


PROBE = [
    ("API.IsAlive", None), ("API.GetProductInfo", None),
    ("API.GetNavigatorItemTree", {"navigatorTreeId": {"type": "ProjectMap"}}),
    ("API.GetNavigatorItemTree", {"navigatorTreeId": {"type": "ViewMap"}}),
    ("API.GetNavigatorItemTree", {"navigatorTreeId": {"type": "LayoutBook"}}),
    ("API.GetNavigatorItemTree", {"navigatorTreeId": {"type": "PublisherSets"}}),
    ("API.GetAllPropertyIds", {"propertyType": "UserDefined"}), ("API.GetAllPropertyNames", None),
    ("API.GetAllClassificationSystems", None),
] + [("API.GetAttributesByType", {"attributeType": k}) for k, _ in ATTR_KINDS]


def probe(ac):
    """Ask each command once; then the getters with the first ids found. → probe dict."""
    results = {}
    for cmd, params in PROBE:
        key = cmd + ("" if params is None else " " + json.dumps(params, sort_keys=True))
        try:
            res = ac.call(cmd, params)
            results[key] = {"ok": True, "keys": sorted(res.keys())[:20]}
            if cmd == "API.GetAttributesByType":
                ids = (res.get("attributeIds") or [])[:3]
                getter = dict(ATTR_KINDS)[params["attributeType"]]
                try:
                    g = ac.call(getter, {"attributeIds": ids})
                    results["API." + getter] = {"ok": True, "sample": g}
                except ApiError as e:
                    results["API." + getter] = {"ok": False, "error": e.code, "message": e.message}
            if cmd == "API.GetAllPropertyIds":
                ids = (res.get("propertyIds") or [])[:3]
                try:
                    results["API.GetDetailsOfProperties"] = {"ok": True, "sample": ac.call("GetDetailsOfProperties", {"properties": ids})}
                except ApiError as e:
                    results["API.GetDetailsOfProperties"] = {"ok": False, "error": e.code, "message": e.message}
            if cmd == "API.GetNavigatorItemTree" and params["navigatorTreeId"]["type"] == "ViewMap":
                items = [i for i, _ in walk((res.get("navigatorTree") or {}).get("rootItem") or {}, [], [])][:3]
                results["sample ViewMap items"] = items
                try:
                    results["API.GetDetailsOfNavigatorItems"] = {"ok": True, "sample": ac.call("GetDetailsOfNavigatorItems", {"navigatorItemIds": [{"navigatorItemId": i.get("navigatorItemId")} for i in items]})}
                except ApiError as e:
                    results["API.GetDetailsOfNavigatorItems"] = {"ok": False, "error": e.code, "message": e.message}
            if cmd == "API.GetProductInfo":
                results["product"] = res
        except ApiError as e:
            results[key] = {"ok": False, "error": e.code, "message": e.message}
    for name in ("GetProjectInfo", "GetStories", "GetAddOnVersion"):
        try:
            results["Tapir." + name] = {"ok": True, "sample": ac.tapir(name)}
        except ApiError as e:
            results["Tapir." + name] = {"ok": False, "error": e.code, "message": e.message}
    return {"format": "cadcraft-archicad-probe", "version": 1, "tool": VERSION, "port": ac.port,
            "when": datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z", "results": results, "calls": ac.calls}


def build(ac, args):
    notes = []
    info = safe(lambda: ac.call("GetProductInfo"), notes, "Archicad version") or {}
    proj = safe(lambda: ac.tapir("GetProjectInfo"), notes, "Project name (Tapir GetProjectInfo)") or {}
    stories = collect_stories(ac, notes)
    views = safe(lambda: collect_views(ac, notes, stories), notes, "View map") or []
    lay = safe(lambda: collect_layouts(ac, notes), notes, "Layout book") or ([], [])
    manifest = {
        "format": FORMAT, "version": 1,
        "generator": {"name": "archicad-bundle", "version": VERSION,
                      "host": clean({"program": "Archicad", "version": str(info.get("version", "")), "build": str(info.get("buildNumber", "")),
                                     "language": info.get("languageCode")})},
        "project": clean({"name": args.name or proj.get("projectName") or "Project", "file": os.path.basename(proj.get("projectPath") or ""),
                          "kind": args.kind, "exported_at": datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"}),
        "model": {"file": "model/model.ifc", "translator": args.translator, "guids": "kept"},
        "stories": stories, "views": views, "layouts": lay[0], "masters": lay[1],
        "properties": collect_properties(ac, notes), "classifications": collect_classifications(ac, notes),
        "standards": collect_standards(ac, notes), "notes": notes,
    }
    work = tempfile.mkdtemp(prefix="cc-bundle-")
    try:
        published = [] if args.no_publish else (safe(lambda: publish(ac, args.publisher_set, work, notes), notes,
                                                     "Publishing \"%s\"" % args.publisher_set) or [])
        if args.files:
            published += [os.path.abspath(p) for p in args.files]
        if not any(p.lower().endswith(".ifc") for p in published):
            raise SystemExit("No IFC file was published. Check the publisher set \"%s\" has the model as IFC (README.md, Use step 2), "
                             "or pass the IFC with --files." % args.publisher_set)
        out = os.path.join(args.out, slug(manifest["project"]["name"]) + ".ccbundle.zip")
        final = clean(manifest)
        final["notes"] = notes
        final.setdefault("model", {})["file"] = "model/model.ifc"
        pack(final, published, out, probe={"calls": ac.calls})
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write a CadCraft project bundle from the project open in Archicad.")
    ap.add_argument("--probe", action="store_true", help="only ask which API commands answer; writes probe.json")
    ap.add_argument("--out", default=".", help="folder for the bundle (or probe.json)")
    ap.add_argument("--port", type=int, help="Archicad's JSON API port (default: the first that answers from 19723)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--publisher-set", default="CadCraft bundle", help="the publisher set that writes IFC + layout PDFs + DWGs")
    ap.add_argument("--no-publish", action="store_true", help="do not publish; use --files instead")
    ap.add_argument("--files", nargs="*", help="files already exported (IFC, layout PDFs/DWGs) to pack")
    ap.add_argument("--name", help="project name (default: from Archicad)")
    ap.add_argument("--kind", choices=["project", "archive", "template"], default="project")
    ap.add_argument("--translator", default=None, help="the IFC translator's name, written into the manifest")
    args = ap.parse_args(argv)
    ac = Archicad(args.port, args.host)
    os.makedirs(args.out, exist_ok=True)
    if args.probe:
        p = os.path.join(args.out, "probe.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(probe(ac), f, indent=2, ensure_ascii=False)
        print("wrote", p)
        return 0
    print("wrote", build(ac, args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
