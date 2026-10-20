# SPDX-License-Identifier: Apache-2.0
"""
A stand-in for Archicad's JSON API, for testing archicad_bundle.py on a machine without Archicad. TEST ONLY.

Its answers follow the shapes in Graphisoft's JSON API reference as we read them; they are NOT recordings of a real
Archicad. A --probe run on a real Archicad is what tells us the real shapes; this server is then updated to match.

    python3 fake_archicad.py --port 19731 --ifc model.ifc --pdf sheet.pdf --dwg sheet.dwg [--old]

--old answers like an older Archicad: no GetDetailsOfProperties, no GetDetailsOfNavigatorItems, no layer combinations,
no Tapir add-on (so the exporter must fall back and write notes).
"""
import argparse
import json
import os
import shutil
from http.server import BaseHTTPRequestHandler, HTTPServer

G = lambda n: "00000000-0000-0000-0000-%012d" % n  # noqa: E731

LAYERS = ["Grundstück & Umgebung", "Treppen", "Innenwände", "Außenwände", "Decken", "Dächer", "Räume"]


def nav(n, name, typ, prefix="", children=None, source=None):
    item = {"navigatorItemId": {"guid": G(n)}, "prefix": prefix, "name": name, "type": typ, "children": children or []}
    if source:
        item["sourceNavigatorItemId"] = {"guid": G(source)}
    return {"navigatorItem": item}


VIEWMAP = nav(1, "View Map", "ViewMapRootItem", children=[
    nav(10, "Grundrisse", "FolderItem", children=[
        nav(11, "Erdgeschoss", "StoryItem", "00"),
        nav(12, "Dachgeschoss", "StoryItem", "01"),
    ]),
    nav(20, "Schnitte", "FolderItem", children=[nav(21, "Schnitt A", "SectionItem", "S-01")]),
    nav(30, "3D Gesamt", "PerspectiveItem"),
])
LAYOUTBOOK = nav(2, "Layout Book", "LayoutBookRootItem", children=[
    nav(40, "A3 quer", "MasterLayoutItem"),
    nav(41, "Ausführung", "SubSetItem", children=[
        nav(42, "Grundriss EG", "LayoutItem", "A-101", children=[nav(43, "00 Erdgeschoss", "DrawingItem", source=11)]),
        nav(44, "Grundriss DG", "LayoutItem", "A-102", children=[nav(45, "01 Dachgeschoss", "DrawingItem", source=12)]),
    ]),
])


class Fake:
    def __init__(self, a):
        self.a = a

    def answer(self, cmd, p):
        a = self.a
        if cmd == "API.IsAlive":
            return {"isAlive": True}
        if cmd == "API.GetProductInfo":
            return {"version": 25 if a.old else 27, "buildNumber": 3001, "languageCode": "INT"}
        if cmd == "API.GetNavigatorItemTree":
            t = p["navigatorTreeId"]["type"]
            root = {"ViewMap": VIEWMAP, "LayoutBook": LAYOUTBOOK}.get(t, nav(3, t, "UndefinedItem"))
            return {"navigatorTree": {"rootItem": root["navigatorItem"]}}
        if cmd == "API.GetDetailsOfNavigatorItems" and not a.old:
            out = []
            for x in p["navigatorItemIds"]:
                g = x["navigatorItemId"]["guid"]
                n = int(g[-12:])
                d = {"navigatorItemId": {"guid": g}, "drawingScale": 0.01, "layerCombination": "Entwurf ohne Umgebung", "modelViewOptions": "Entwurf"}
                if n in (11, 12):
                    d["floorNumber"] = n - 11
                if n == 30:
                    d = {"navigatorItemId": {"guid": g}, "layerCombination": "Alles"}
                out.append({"details": d})
            return {"navigatorItemDetailsList": out}
        if cmd == "API.GetAttributesByType":
            k = p["attributeType"]
            if a.old and k == "LayerCombination":
                return None
            n = {"Layer": len(LAYERS), "LayerCombination": 2, "PenTable": 1, "Line": 2, "Fill": 2, "Surface": 1, "Composite": 1, "BuildingMaterial": 2}.get(k, 0)
            return {"attributeIds": [{"attributeId": {"guid": G(1000 + 100 * list(ATTR).index(k) + i)}} for i in range(n)]}
        if cmd in GETTERS:
            k = GETTERS[cmd]
            if a.old and k == "LayerCombination":
                return None
            ids = [x["attributeId"]["guid"] for x in p["attributeIds"]]
            return {"attributes": [ATTR[k](g, i) for i, g in enumerate(ids)]}
        if cmd == "API.GetAllPropertyIds":
            return {"propertyIds": [{"propertyId": {"guid": G(5000 + i)}} for i in range(3)]}
        if cmd == "API.GetDetailsOfProperties" and not a.old:
            defs = [("Brandschutz", "Feuerwiderstand", "string", "singleEnum", ["F30", "F90"]),
                    ("Brandschutz", "Rauchdicht", "boolean", "single", []),
                    ("Raum", "Nutzung", "string", "single", [])]
            return {"propertyDefinitions": [{"propertyDefinition": {
                "propertyId": {"guid": G(5000 + i)}, "group": {"propertyGroupId": {"guid": G(6000 + (0 if g == "Brandschutz" else 1))}, "name": g},
                "name": n, "description": "", "valueType": vt, "collectionType": ct,
                "possibleEnumValues": [{"enumValue": {"displayValue": v}} for v in vals],
                "availability": [{"classificationItemId": {"guid": G(7001)}}]}} for i, (g, n, vt, ct, vals) in enumerate(defs)]}
        if cmd == "API.GetAllPropertyNames":
            return {"properties": [{"type": "UserDefined", "localizedName": ["Brandschutz", "Feuerwiderstand"]},
                                   {"type": "BuiltIn", "nonLocalizedName": "General_ElementID"}]}
        if cmd == "API.GetAllClassificationSystems":
            return {"classificationSystems": [{"classificationSystemId": {"guid": G(7000)}, "name": "ARCHICAD Classification", "version": "v 2.0", "source": "", "date": "2019-01-01"}]}
        if cmd == "API.GetAllClassificationsInSystem":
            return {"classificationItems": [{"classificationItem": {"classificationItemId": {"guid": G(7001)}, "id": "Wall", "name": "Wall", "children": [
                {"classificationItem": {"classificationItemId": {"guid": G(7002)}, "id": "Wall.Partition", "name": "Partition", "children": []}}]}}]}
        if cmd == "API.PublishPublisherSet":
            if p.get("publisherSetName") != "CadCraft bundle":
                raise KeyError("no such publisher set")
            out = p["outputPath"]
            shutil.copy(a.ifc, os.path.join(out, "FZK-Haus.ifc"))
            for stem in ("A-101", "A-102"):
                shutil.copy(a.pdf, os.path.join(out, stem + ".pdf"))
            shutil.copy(a.dwg, os.path.join(out, "A-101.dwg"))
            shutil.copy(a.pdf, os.path.join(out, "Unmatched sheet.pdf"))
            return {}
        if cmd == "API.ExecuteAddOnCommand" and not a.old:
            name = p["addOnCommandId"]["commandName"]
            if name == "GetProjectInfo":
                return {"addOnCommandResponse": {"projectName": "FZK-Haus", "projectPath": "C:/Projects/FZK-Haus.pln", "isUntitled": False}}
            if name == "GetStories":
                return {"addOnCommandResponse": {"stories": [{"index": 0, "uName": "Erdgeschoss", "level": 0.0}, {"index": 1, "uName": "Dachgeschoss", "level": 2.7}]}}
            if name == "GetAddOnVersion":
                return {"addOnCommandResponse": {"version": "1.0.0"}}
        return None


def layer(g, i):
    return {"layerAttribute": {"attributeId": {"guid": g}, "name": LAYERS[i], "isHidden": False, "isLocked": i == 0, "isWireframe": False, "intersectionGroupNumber": i + 1}}


def combo(g, i):
    name = ["Alles", "Entwurf ohne Umgebung"][i]
    return {"layerCombinationAttribute": {"attributeId": {"guid": g}, "name": name, "layerAttributes": [
        {"attributeId": {"guid": G(1000 + j)}, "isHidden": (i == 1 and j == 0), "isLocked": False, "isWireframe": False} for j in range(len(LAYERS))]}}


ATTR = {
    "Layer": layer,
    "LayerCombination": combo,
    "PenTable": lambda g, i: {"penTableAttribute": {"attributeId": {"guid": g}, "name": "Architektur", "pens": [
        {"index": 1, "color": {"red": 0, "green": 0, "blue": 0}, "width": 0.13}, {"index": 3, "color": {"red": 1, "green": 0, "blue": 0}, "width": 0.18}]}},
    "Line": lambda g, i: {"lineAttribute": {"attributeId": {"guid": g}, "name": ["Durchgehend", "Gestrichelt"][i], "lineType": ["SolidLine", "DashedLine"][i]}},
    "Fill": lambda g, i: {"fillAttribute": {"attributeId": {"guid": g}, "name": ["Ziegel", "Vollfüllung"][i], "subType": ["VectorFill", "SolidFill"][i]}},
    "Surface": lambda g, i: {"surfaceAttribute": {"attributeId": {"guid": g}, "name": "Putz weiß", "surfaceColor": {"red": 0.95, "green": 0.95, "blue": 0.94}, "transparency": 0}},
    "Composite": lambda g, i: {"compositeAttribute": {"attributeId": {"guid": g}, "name": "Außenwand 300", "compositeSkins": [
        {"buildingMaterialId": {"attributeId": {"guid": G(1700)}}, "thickness": 0.24, "isCore": True},
        {"buildingMaterialId": {"attributeId": {"guid": G(1701)}}, "thickness": 0.06, "isCore": False}]}},
    "BuildingMaterial": lambda g, i: {"buildingMaterialAttribute": {"attributeId": {"guid": g}, "name": ["Ziegel", "Dämmung"][i]}},
}
GETTERS = {"API.Get%sAttributes" % k: k for k in ATTR}


class Handler(BaseHTTPRequestHandler):
    fake = None

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        cmd, p = body.get("command"), body.get("parameters") or {}
        try:
            res = self.fake.answer(cmd, p)
            out = {"succeeded": True, "result": res} if res is not None else {"succeeded": False, "error": {"code": 4001, "message": "Unknown command: " + str(cmd)}}
        except Exception as e:
            out = {"succeeded": False, "error": {"code": 4002, "message": str(e)}}
        data = json.dumps(out).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=19731)
    ap.add_argument("--ifc", required=True)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--dwg", required=True)
    ap.add_argument("--old", action="store_true")
    a = ap.parse_args()
    Handler.fake = Fake(a)
    srv = HTTPServer(("127.0.0.1", a.port), Handler)
    print("ready", a.port, flush=True)
    srv.serve_forever()
