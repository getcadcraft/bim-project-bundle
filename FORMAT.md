<!-- SPDX-License-Identifier: Apache-2.0 -->
# Project bundle format (cadcraft-project-bundle 1)

## 1. What is in a bundle

One zip (stored or deflated; no zip64), extension `.zip` or `.ccbundle`, with `manifest.json` at its root:

```
manifest.json            the format below (UTF-8 JSON, ≤ 32 MB)
model/<name>.ifc         the model (IFC4 Reference View or IFC2X3 CV 2.0; zones as spaces; properties; classifications)
layouts/<id>.pdf         one PDF per layout (optional; or one book PDF, see layouts[].pdf)
layouts/<id>.dwg         one DWG per layout (optional)
views/<id>.dwg|.pdf      a view saved on its own (optional)
probe.json               what the exporter asked Archicad and what answered (optional; for us, not read by the apps)
```

Paths in the manifest are zip entry names: relative, `/`-separated, no `..`, no leading `/`, ≤ 255 characters. A path the
zip does not hold is reported, never guessed.

## 2. `manifest.json` (format `cadcraft-project-bundle`, version 1)

Every block but `format`, `version` and `model` is optional: the exporter writes what Archicad gave it and says what it did
not get in `notes`. Readers ignore unknown keys. Lengths on paper are **mm**, model heights **m**, angles **degrees**.

```jsonc
{
  "format": "cadcraft-project-bundle",
  "version": 1,
  "generator": { "name": "archicad-bundle", "version": "0.1.0", "host": { "program": "Archicad", "version": "27", "build": "3001", "language": "INT" } },
  "project": { "name": "FZK-Haus", "file": "FZK-Haus.pln", "kind": "project|archive|template", "exported_at": "2026-10-16T12:00:00Z" },
  "model": { "file": "model/FZK-Haus.ifc", "schema": "IFC4", "translator": "IFC4 Reference View", "guids": "kept" },
  "stories": [ { "index": 0, "name": "Erdgeschoss", "elevation": 0.0, "ifc": "2eyxpyOx95m90jmsXLOuR0" } ],
  "views": [ {
      "id": "guid", "name": "00 Erdgeschoss", "path": ["Plans"], "kind": "plan|section|elevation|interior_elevation|detail|worksheet|3d|document_3d|schedule|list|other",
      "story": 0, "scale": 100, "layer_combination": "01 Entwurf", "model_view_options": "Construction",
      "pen_set": "Architectural", "structure_display": "Entire Model", "dimension_style": "Metric", "renovation_filter": "New",
      "graphic_override": "None", "zoom": { "x0": 0, "y0": 0, "x1": 12, "y1": 10 }, "file": "views/<id>.dwg"
  } ],
  "layouts": [ {
      "id": "guid", "name": "Grundriss EG", "number": "A-101", "path": ["Subset A"], "master": "A3 Landscape",
      "size": { "w": 420, "h": 297 }, "pdf": "layouts/A-101.pdf", "pdf_page": 1, "dwg": "layouts/A-101.dwg",
      "drawings": [ { "name": "00 Erdgeschoss", "view": "guid", "scale": 100, "x": 20, "y": 20, "w": 300, "h": 200 } ]
  } ],
  "masters": [ { "id": "guid", "name": "A3 Landscape", "size": { "w": 420, "h": 297 } } ],
  "numbering": { "layouts": "by subset", "prefix": "A-" },
  "properties": {
    "groups": [ { "id": "guid", "name": "Fire safety" } ],
    "definitions": [ {
        "id": "guid", "group": "guid", "name": "Fire rating", "type": "string|integer|number|length|area|volume|angle|boolean|enum|list",
        "unit": null, "values": ["EI30", "EI60"], "default": "EI30", "description": "",
        "classifications": ["guid"], "ifc": { "pset": "Pset_WallCommon", "name": "FireRating" }
    } ]
  },
  "classifications": [ { "id": "guid", "name": "ARCHICAD Classification", "version": "26", "source": "", "items": [
      { "id": "guid", "code": "Wall", "name": "Wall", "children": [] } ] } ],
  "standards": {
    "layers": [ { "name": "Innenwände", "hidden": false, "locked": false, "wireframe": false, "group": 1 } ],
    "layer_combinations": [ { "name": "01 Entwurf", "layers": { "Innenwände": { "hidden": false, "locked": false, "wireframe": false } } } ],
    "pen_sets": [ { "name": "Architectural", "pens": [ { "index": 1, "color": "#000000", "weight": 0.13 } ] } ],
    "line_types": [ { "name": "Dashed", "kind": "solid|dashed|symbol", "pattern": [3, 1.5] } ],
    "fills": [ { "name": "Brick", "kind": "vector|symbol|solid|empty|image|gradient|other" } ],
    "surfaces": [ { "name": "Paint-White", "color": "#f4f4f2", "transparency": 0 } ],
    "composites": [ { "name": "Ext Wall 300", "skins": [ { "material": "Brick", "thickness": 0.24, "core": true } ] } ],
    "materials": [ { "name": "Brick", "fill": "Brick", "surface": "Brick red" } ]
  },
  "notes": [ "Layer combinations: this Archicad version's API does not list them (GetLayerCombinationAttributes missing)." ]
}
```

Rules the reader applies (`bimcraft/dev/bundle-open.js` `readManifest`): untrusted input; strings ≤ 500 characters (names ≤
255), lists ≤ 20 000 rows (views/layouts ≤ 5 000), nesting ≤ 32, numbers finite, colours `#rrggbb`; a row with a bad field
keeps its other fields and the field is dropped with a note; `views[].story` is a story index, `layouts[].drawings[].view` a
view id — unknown ones are kept as text and noted.
