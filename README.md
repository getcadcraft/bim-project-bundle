<!-- SPDX-License-Identifier: Apache-2.0 -->
# Project bundle exporter

Write a **project bundle** (`.ccbundle.zip`) from a project open in Archicad, and open the whole project in
[CadCraft BimCraft](https://getcadcraft.com): the model, its views, its layouts and its property groups.

> **Preview.** This script has been tested against a stand-in for Archicad's JSON API, not yet against every Archicad
> version. If something does not come through, please open an issue and attach the `probe.json` from step 3 (it holds
> only the API's answers about your setup, no model and no drawings).

A bundle holds:

- the model as IFC (with the IFC translator you choose: zones as spaces, properties, classifications),
- every layout as PDF and DWG,
- `manifest.json` with what IFC leaves out: the view map (each view's storey, layer combination, scale, model view
  options), the layout book (layouts, the drawings placed on them, masters), property definitions with their groups and
  classifications, and the standards (layers, layer combinations, pen sets, line types, fills, surfaces, composites,
  building materials) as far as Archicad's JSON API gives them.

It is one Python file using the standard library only, so it runs in Archicad's Python palette or from a terminal with
nothing to install. It talks to the JSON API Archicad itself serves on `127.0.0.1`; it never reads `.pln` / `.tpl` files
and uses no Graphisoft or ODA SDK. Whatever the API of your Archicad version does not give is listed in the manifest's
`notes`, never guessed.

## You need

- Archicad 24 or later (27 or 28 best), on Windows or Mac.
- Python 3.8 or later (`python --version`), or Archicad's Python palette.
- Optional: the free [Tapir add-on](https://github.com/ENZYME-APD/tapir-archicad-automation) for the project name and
  the storey list (Releases → the build for your Archicad → copy the `.apx` into Archicad's Add-Ons folder → restart).

## Use

1. **Download** [`archicad_bundle.py`](archicad_bundle.py) (Raw → Save as) into a folder, e.g. `C:\cc-bundle\`.
2. **Make a publisher set once** per project or template. In Archicad: Navigator › Publisher Sets → New Publisher Set…,
   named exactly **CadCraft bundle**, holding:
   - the **3D** view, Format **IFC**, with your IFC translator ("IFC4 Reference View" if you have it, else "Coordination
     View 2.0"). In the translator: zones as spaces, properties "All", classifications on, GUIDs kept.
   - **every layout**, Format **PDF**,
   - **every layout** again, Format **DWG**.

   In the set's Publish Properties: Publish to **Files on disk**, folder structure **Flat**, file names = layout number
   or name.
3. **Probe** (first time; it only reads, nothing in the project changes). With the project open:

   ```
   cd C:\cc-bundle
   python archicad_bundle.py --probe --out .
   ```

   It writes `probe.json`: which API commands your Archicad answers.
4. **Make the bundle:**

   ```
   python archicad_bundle.py --out . --translator "IFC4 Reference View"
   ```

   Archicad publishes the set, then the script writes `<project>.ccbundle.zip`.
5. **Open it in BimCraft:** Choose a file (or drop the zip). The **Project bundle** section on the left shows Views,
   Layouts, Property groups, Standards and Notes.

In Archicad's Python palette (Window › Palettes › Python palette), run `archicad_bundle.py` with the same arguments.

### If publishing through the API fails

Publish the **CadCraft bundle** set by hand into a folder, then pack the files:

```
python archicad_bundle.py --out . --no-publish --files <folder>\*.ifc <folder>\*.pdf <folder>\*.dwg
```

### Options

| Option | |
|---|---|
| `--out <folder>` | where the bundle (or `probe.json`) goes |
| `--translator "<name>"` | the IFC translator you picked, recorded in the manifest |
| `--publisher-set "<name>"` | a set with another name than "CadCraft bundle" |
| `--port <n>` | Archicad's JSON API port (default: the first that answers from 19723) |
| `--no-publish --files …` | pack files you exported yourself |
| `--name "<name>"` | project name (default: from Archicad) |
| `--kind project\|archive\|template` | what the bundle came from; a template's bundle mainly carries its standards |

### Messages

- **"No IFC file was published"**: the set has no IFC item (step 2), or another name (use `--publisher-set`).
- **No JSON API answers**: Archicad is not open, or it is older than 24.

## Format

`cadcraft-project-bundle` version 1: see [`FORMAT.md`](FORMAT.md).

## Testing without Archicad

`test/fake_archicad.py` is a stand-in JSON API whose answers follow our reading of Graphisoft's reference (not a
recording of a real Archicad):

```
python3 test/fake_archicad.py --port 19731 --ifc model.ifc --pdf sheet.pdf --dwg sheet.dwg
python3 archicad_bundle.py --port 19731 --out .
```

## License

Apache-2.0 (`LICENSE`).

Archicad is a trademark of Graphisoft SE. This project is not made, endorsed or supported by Graphisoft.
