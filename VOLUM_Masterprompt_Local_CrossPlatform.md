# VOLUM — Master Prompt für Claude Code
## Local-first, Cross-platform Image-to-3D Desktop Application

Du bist der Lead Software Architect, ML Engineer, 3D Graphics Engineer, Desktop Engineer und DevOps Engineer für das Open-Source-Projekt **VOLUM**.

VOLUM ist eine **lokale, native Desktop-Anwendung**, die aus einem oder mehreren Bildern hochwertige 3D-Assets erzeugt.

Das Projekt wird öffentlich auf GitHub entwickelt.

## Zentrale Produktentscheidung

VOLUM ist:

- local-first
- offline-capable
- privacy-first
- Open Source
- cross-platform
- ohne Cloud-Zwang
- ohne VPS
- ohne Remote-Inference
- ohne verpflichtendes Benutzerkonto
- ohne Telemetrie standardmäßig

**Alle Bilder, Zwischenresultate, Model-Inference und erzeugten Assets bleiben standardmäßig auf dem lokalen Rechner.**

Zielplattformen:

1. macOS Apple Silicon
2. Windows
3. Linux

Der primäre Entwicklungsrechner ist ein **Apple-Silicon-Mac (M1)**.

Die Architektur darf deshalb nicht implizit davon ausgehen, dass NVIDIA/CUDA vorhanden ist.

---

# 1. ZIEL

VOLUM soll aus einem oder mehreren Bildern ein tatsächlich nutzbares 3D-Modell erzeugen.

Primärer Workflow:

    Bilder
       ↓
    Input Analysis
       ↓
    Preprocessing
       ↓
    3D Reconstruction / Generation
       ↓
    Geometry Processing
       ↓
    Texture / PBR Processing
       ↓
    Mesh Optimization
       ↓
    Validation
       ↓
    GLB
       ↓
    interaktiver 3D Viewer

Primäres Ergebnis:

    game-/web-/design-taugliches 3D-Asset

Primäres Austauschformat:

    GLB / glTF

Zusätzlich vorbereiten:

    OBJ
    FBX
    STL
    USDZ
    3MF

Diese zusätzlichen Formate müssen nicht alle in V1 vollständig implementiert werden.

---

# 2. WICHTIGE PRODUKTPHILOSOPHIE

VOLUM ist NICHT nur eine Demo für ein einzelnes Image-to-3D-Modell.

VOLUM ist eine **3D-Inference-Plattform mit austauschbaren Model Providern**.

Die Modellebene muss deshalb abstrahiert sein.

Konzeptionell:

    VOLUM
       │
       ▼
    ImageTo3DProvider
       │
       ├── TRELLIS.2
       ├── TRELLIS
       ├── Hunyuan3D
       ├── Stable Fast 3D
       └── zukünftige Modelle

Der Rest der Anwendung darf nicht direkt von einem bestimmten Modell abhängig sein.

---

# 3. TECHNOLOGIEENTSCHEIDUNG

Verwende als Ausgangspunkt:

## Desktop

    Tauri 2

## Frontend

    React
    TypeScript
    Vite

## UI

    Tailwind CSS
    shadcn/ui

## 3D

    Three.js
    React Three Fiber

## Local Application Backend / Engine

    Python

## API / IPC Boundary

Bevorzuge eine lokale, klar definierte Schnittstelle zwischen Tauri/Frontend und Python Engine.

Prüfe, ob localhost HTTP, Unix socket oder Tauri sidecar/IPC für die jeweilige Funktion sinnvoller ist.

Keine unnötige Komplexität.

## Python Packaging

    uv
    pyproject.toml

## Database

    SQLite

Nur verwenden, wenn persistenter State tatsächlich benötigt wird.

## Testing

    pytest
    Vitest
    Playwright

## Versioning

    Semantic Versioning 2.0.0
    Conventional Commits

## CI

    GitHub Actions

## Primary export

    GLB

---

# 4. APP-ARCHITEKTUR

Zielarchitektur:

                         VOLUM
                           │
                 ┌─────────┴─────────┐
                 │                   │
              Tauri 2             VOLUM Core
                 │                   │
          React / Three.js      Python Engine
                                     │
                          ┌──────────┼──────────┐
                          │          │          │
                         MLX        MPS        CUDA
                          │          │          │
                       macOS      macOS      Win/Linux
                          │          │          │
                          └──────────┴──────────┘
                                     │
                              Model Providers
                                     │
                              3D Pipeline
                                     │
                              Mesh / Texture
                                     │
                                  GLB/OBJ

Die genaue Architektur darf verbessert werden, wenn die Recherche zeigt, dass eine andere Lösung objektiv robuster ist.

---

# 5. PLATTFORMSTRATEGIE

## macOS Apple Silicon

Priorität:

    MLX / Metal

Fallback:

    PyTorch MPS

Die Anwendung muss Apple Silicon nativ erkennen.

Architektur:

    arm64

Nicht unter Rosetta laufen, wenn eine native Lösung verfügbar ist.

## Windows

Primär:

    NVIDIA CUDA / PyTorch

Weitere GPU-Unterstützung nur, wenn das jeweilige Modell sie tatsächlich unterstützt.

## Linux

Primär:

    NVIDIA CUDA / PyTorch

Auch hier keine falschen Kompatibilitätsversprechen.

---

# 6. GPU ABSTRAKTION

Implementiere:

    HardwareDetector

Er soll mindestens erkennen:

- OS
- CPU architecture
- CPU
- RAM
- GPU
- VRAM / Unified Memory, soweit zuverlässig ermittelbar
- Metal
- MPS
- CUDA
- CUDA-Version
- PyTorch-Version
- MLX-Verfügbarkeit
- verfügbaren Speicher

Beispiel:

    VOLUM Doctor

    Platform       macOS
    Architecture   arm64
    CPU            Apple M1
    Memory         XX GB
    GPU            Apple GPU
    Metal          available
    MLX            available
    PyTorch MPS    available

    Recommended Runtime:
        MLX

    Compatible Models:
        ...

Unter Windows/Linux entsprechend.

---

# 7. HARDWARE-AWARE MODEL SELECTION

Das System muss erkennen, ob ein Modell auf der vorhandenen Hardware sinnvoll ausgeführt werden kann.

Beispiel:

    Required VRAM: ~24 GB
    Available:     8 GB

Ergebnis:

    Model unavailable on this machine.

Keine automatische Absturzschleife.

Keine erzwungene CPU-Ausführung eines GPU-Modells, wenn das praktisch nicht sinnvoll ist.

---

# 8. MODELLRECHERCHE

Vor der eigentlichen Implementierung musst du den aktuellen Stand recherchieren.

Mindestens prüfen:

1. Microsoft TRELLIS.2
2. Microsoft TRELLIS
3. Tencent Hunyuan3D-2.1
4. neuere Hunyuan3D-Versionen, falls verfügbar
5. Stable Fast 3D
6. weitere relevante Open-Source-/Open-Weight-Modelle
7. relevante Multi-View-Reconstruction-Verfahren

Da sich der Bereich schnell entwickelt, darfst du dich nicht auf diese Liste beschränken.

Prüfe aktuelle Quellen.

Priorität:

- offizielle GitHub-Repositories
- offizielle Model Cards
- offizielle Dokumentation
- Papers / arXiv der Autoren

Sekundärquellen nur ergänzend.

---

# 9. MODELLBEWERTUNG

Erstelle:

    docs/model-evaluation.md

Bewerte:

- Geometriequalität
- Texturqualität
- PBR
- UV
- Topologie
- Single Image
- Multi Image
- Runtime
- RAM
- VRAM
- Apple Silicon Support
- CUDA Support
- Windows Support
- Linux Support
- macOS Support
- Lizenz
- kommerzielle Nutzbarkeit
- Modellgröße
- Downloadgröße
- Wartungszustand
- Integrationsaufwand
- Output-Formate
- Reproduzierbarkeit

Bewerte nicht nur nach Marketingangaben.

Wenn Benchmarks verfügbar sind, dokumentiere deren Bedingungen.

---

# 10. LIZENZEN

Das öffentliche GitHub-Repository darf keine Lizenzprobleme verstecken.

Für jeden integrierten Provider dokumentieren:

    repository
    source
    code license
    model license
    weights license
    commercial use
    attribution
    restrictions
    redistribution
    required notices

Wenn Lizenzbedingungen unklar sind:

    UNKNOWN

Nicht behaupten, dass ein Modell kommerziell nutzbar ist, wenn dies nicht eindeutig belegt ist.

Modellgewichte niemals ungeprüft ins Repository committen.

---

# 11. MODEL PROVIDER INTERFACE

Implementiere eine gemeinsame Schnittstelle, z.B.:

    ImageTo3DProvider

Sie sollte Fähigkeiten und Anforderungen beschreiben.

Beispiel:

    SINGLE_IMAGE
    MULTI_IMAGE
    TEXTURE
    PBR
    UV
    HIGH_RESOLUTION
    FAST_INFERENCE

Zusätzlich:

    supported_platforms
    supported_runtimes
    minimum_memory
    estimated_vram
    model_version
    license_metadata

Methoden sinngemäß:

    validate_environment()
    estimate_resources()
    generate()
    cleanup()
    metadata()

---

# 12. MODEL MANAGER

Implementiere einen lokalen Model Manager.

Funktionen:

    list_models()
    get_model()
    check_installed()
    install()
    verify()
    remove()
    disk_usage()

Modelle werden lokal gecacht.

Nicht automatisch mehrere zig Gigabyte herunterladen, ohne dass der Nutzer dies auslöst.

UI:

    Models

    TRELLIS.2
    Status: Not installed
    Size: ...
    Requirements: ...
    License: ...
    [Install]

---

# 13. SINGLE IMAGE MODE

Unterstütze:

    1 Bild → 3D

Der Nutzer muss wissen:

Single-image reconstruction enthält geschätzte Geometrie für nicht sichtbare Bereiche.

Nicht behaupten, dass eine einzelne Aufnahme eine physikalisch exakte Rekonstruktion ermöglicht.

---

# 14. MULTI IMAGE MODE

Unterstütze mehrere Bilder desselben Objekts.

Mögliche Inputs:

    3
    5
    10
    20
    50+

Die Anwendung soll:

1. Bilder validieren
2. Bilder normalisieren
3. Objekt erkennen
4. Hintergrund prüfen
5. Views analysieren
6. schlechte/duplizierte Bilder erkennen
7. Multi-View-Modell verwenden, sofern verfügbar
8. Ergebnis validieren

Wenn ein Provider Multi-Image nicht unterstützt:

    MULTI_IMAGE = false

Nicht simulieren.

---

# 15. INPUT ANALYSIS

Implementiere eine modulare Analyse:

- Bildauflösung
- Seitenverhältnis
- Dateigröße
- EXIF
- Bildqualität
- Objektgröße
- Hintergrund
- Schärfe
- Belichtung
- View similarity
- Anzahl Bilder

Warnungen beispielsweise:

    Images appear to show nearly identical viewpoints.

oder:

    Object occupies too little of the image.

---

# 16. PREPROCESSING

Modulare Pipeline:

    Input
      ↓
    EXIF handling
      ↓
    validation
      ↓
    segmentation
      ↓
    background removal
      ↓
    crop / bounding box
      ↓
    normalization
      ↓
    model-specific preprocessing

Originaldateien niemals überschreiben.

---

# 17. JOB SYSTEM

3D-Inference ist langlaufend.

Frontend darf nicht auf einen langen synchronen Request warten.

Verwende lokale Jobs.

Status:

    QUEUED
    PREPROCESSING
    RECONSTRUCTING
    TEXTURING
    OPTIMIZING
    VALIDATING
    COMPLETED
    FAILED
    CANCELLED

Jeder Job besitzt:

    id
    status
    progress
    stage
    model
    model_version
    created_at
    started_at
    finished_at
    input_files
    artifacts
    parameters
    error
    logs

---

# 18. PROGRESS

Keine Fake-Progress-Bar.

Wenn das Modell keinen echten Prozentwert liefert:

Nutze Pipeline-Stages.

Beispiel:

    Preparing images
    Reconstructing geometry
    Generating materials
    Optimizing mesh
    Validating asset
    Complete

Optional kann innerhalb einer Stage ein unbekannter Fortschritt angezeigt werden, aber keine erfundenen Prozentwerte.

---

# 19. PIPELINE

Die Kernpipeline muss modular sein:

    Input
      ↓
    Analyze
      ↓
    Preprocess
      ↓
    Reconstruction
      ↓
    Geometry processing
      ↓
    Texture/PBR
      ↓
    Optimization
      ↓
    Validation
      ↓
    Export
      ↓
    Viewer

Jede Pipeline Stage muss isoliert testbar sein.

---

# 20. MESH PROCESSING

Prüfe geeignete Tools, beispielsweise:

- trimesh
- Open3D
- PyMeshLab
- Blender headless
- assimp

Verwende nicht blind alle Bibliotheken.

Wähle nur die Werkzeuge, die tatsächlich benötigt werden.

Mögliche Operationen:

    cleanup
    normals
    decimation
    remeshing
    manifold checks
    duplicate vertex removal
    degenerate face removal
    UV processing
    scale normalization

---

# 21. QUALITY VALIDATION

Jedes erzeugte Asset muss validiert werden.

Prüfe mindestens:

- GLB/OBJ lesbar
- Mesh vorhanden
- Vertices
- Faces
- NaN
- Infinity
- bounding box
- normals
- UV
- materials
- texture references
- missing textures
- degenerate faces
- non-manifold geometry
- polygon count
- dimensions

Erzeuge:

    quality_report.json

Beispiel:

    {
      "valid": true,
      "vertices": 124381,
      "triangles": 241102,
      "materials": 3,
      "textures": 3,
      "uv_valid": true,
      "non_manifold_edges": 0,
      "degenerate_faces": 2
    }

---

# 22. 3D VIEWER

Der Viewer muss professionell sein.

Unterstütze:

- Orbit
- Pan
- Zoom
- Reset
- Auto Rotate
- Grid
- Environment Lighting
- Wireframe
- Solid
- Material Preview
- Texture Preview
- Background toggle

Zusätzlich:

- dimensions
- vertices
- triangles
- materials
- textures
- file size

Große Modelle nicht unnötig mehrfach laden.

---

# 23. UI/UX

VOLUM soll wie ein professionelles Desktop-Tool wirken.

Nicht wie ein generischer AI-Chat.

Hauptworkflow:

    Upload
      ↓
    Configure
      ↓
    Generate
      ↓
    Processing
      ↓
    Inspect
      ↓
    Export

Primäre Oberfläche:

    VOLUM

    Drop images here

    Reconstruction
        Automatic
        Fast
        Balanced
        High
        Maximum

    Images:
        1 / 5 / 10 / ...

    [ Generate 3D ]

Nach Fertigstellung:

    3D VIEWER

    Asset information

    Vertices
    Triangles
    Materials
    Textures
    Dimensions

    [ Export GLB ]
    [ Export OBJ ]

---

# 24. ADVANCED MODE

Normale Nutzer müssen keine ML-Begriffe kennen.

Advanced Mode darf zeigen:

    Model
    Runtime
    Resolution
    Seed
    Steps
    Texture Resolution
    Mesh Density
    Decimation

Defaults sollen sinnvoll sein.

---

# 25. PRIVACY

VOLUM soll standardmäßig komplett lokal funktionieren.

Keine:

- Cloud uploads
- analytics
- telemetry
- external image APIs
- external inference

ohne explizite zukünftige Opt-in-Funktion.

Dokumentiere dies in:

    docs/privacy.md

---

# 26. NETWORK ACCESS

VOLUM darf für folgende Zwecke Netzwerkzugriff benötigen:

- erstmaliger Model Download
- Update/Model metadata, wenn implementiert
- explizit vom Nutzer ausgelöste Aktionen

Die eigentliche Verarbeitung muss lokal erfolgen.

Zeige bei Model Downloads klar:

    Downloading model weights...

Nach Installation soll das Modell offline verwendbar sein.

---

# 27. STORAGE

Verwende einen klaren lokalen Datenpfad.

Nicht einfach Dateien irgendwo im aktuellen Working Directory verteilen.

Beispiel:

    VOLUM data directory
        models/
        projects/
        jobs/
        cache/
        logs/
        exports/

Plattformgerechte Standardpfade verwenden.

---

# 28. PROJECTS

VOLUM soll Projekte speichern können.

Ein Projekt enthält:

    project.json
    source images
    generation configuration
    generated assets
    quality reports
    metadata

Ein Projekt muss reproduzierbar sein.

---

# 29. ASSET METADATA

Jedes Ergebnis bekommt:

    asset.json

mit:

    id
    created_at
    source_images
    model
    model_version
    runtime
    parameters
    preprocessing
    geometry_statistics
    texture_statistics
    validation
    processing_time
    hardware

---

# 30. DETERMINISTISCHE REPRODUZIERBARKEIT

Wenn das verwendete Modell einen Seed unterstützt:

    seed

speichern.

Zusätzlich:

- Modellversion
- Runtime
- Parameter
- Input hash
- preprocessing configuration

speichern.

Wenn vollständige Deterministik technisch nicht garantiert werden kann, dokumentieren.

---

# 31. CACHE

Identische Inputs sollen nicht unnötig erneut verarbeitet werden.

Erzeuge Hash aus:

    source image hashes
    model
    model version
    runtime
    parameters
    preprocessing configuration

Cache:

    input/config hash → artifact

Der Nutzer kann den Cache löschen.

---

# 32. CLI

VOLUM soll neben der GUI eine CLI besitzen.

Beispiele:

    volum doctor

    volum models

    volum models install trellis2

    volum generate image.jpg

    volum generate image1.jpg image2.jpg image3.jpg

    volum validate model.glb

    volum benchmark

    volum cleanup

CLI und GUI müssen dieselbe Core-Pipeline verwenden.

Keine doppelte Business-Logik.

---

# 33. DOCTOR

Implementiere:

    volum doctor

Er soll erkennen:

    OS
    architecture
    CPU
    RAM
    GPU
    VRAM / memory
    Metal
    MPS
    CUDA
    MLX
    PyTorch
    installed models
    disk space
    incompatible models

Beispiel:

    VOLUM Doctor

    Platform ............. macOS
    Architecture ......... arm64
    Apple Silicon ........ detected
    MLX .................. OK
    PyTorch MPS .......... OK
    CUDA ................. N/A

    Recommended runtime:
        MLX

    Recommended models:
        ...

---

# 34. BENCHMARKING

Erstelle:

    benchmarks/

Teste Provider mit denselben Inputs.

Metriken:

- inference time
- preprocessing time
- postprocessing time
- total time
- peak memory
- output size
- vertices
- triangles
- textures
- validation failures

Qualitative Bewertungen dürfen separat dokumentiert werden.

Erzeuge:

    benchmark-results.json

und:

    docs/benchmarks.md

---

# 35. AUTOMATISCHE QUALITÄTSHEURISTIK

Wenn sinnvoll, entwickle eine Quality Score Pipeline.

Beispielsweise:

    Geometry
    Texture
    UV
    Topology
    Completeness
    Runtime

Aber:

Keine mathematisch bedeutungslose "AI Quality Score"-Zahl.

Wenn ein Score eingeführt wird, muss dokumentiert werden, wie er berechnet wird und was er tatsächlich aussagt.

---

# 36. TESTING

## Unit Tests

Teste:

- model registry
- provider interface
- configuration
- hashing
- preprocessing
- validation
- exporters
- job state machine
- hardware detection

## Integration Tests

Teste:

    API / IPC
    job creation
    job lifecycle
    storage
    provider loading

## E2E

Mindestens:

    import image
      ↓
    generate job
      ↓
    pipeline
      ↓
    GLB
      ↓
    validation
      ↓
    viewer

Der E2E-Test darf nicht nur einen Fake-GLB erzeugen.

---

# 37. MOCKS

Mocks dürfen für Unit Tests verwendet werden.

Aber:

Produktionspfad niemals mit Mock-Implementierungen ausstatten.

Keine:

    fake.glb
    fake progress
    fake model
    fake validation

Wenn echte Inference auf CI nicht möglich ist:

Trenne:

    CPU-safe integration tests

von:

    GPU smoke tests

---

# 38. GPU SMOKE TESTS

Erstelle einen optionalen echten GPU-Test.

Beispiel:

    volum benchmark --model trellis2 --smoke

Dieser Test muss echte Inference durchführen.

Er darf in CI optional sein, wenn keine GPU verfügbar ist.

---

# 39. PACKAGING

Ziel:

## macOS

    .dmg

Native Apple Silicon Version.

Wenn möglich:

    arm64

## Windows

    installer / executable

## Linux

    AppImage

oder eine vergleichbar portable Distribution.

Packaging so gestalten, dass Python Engine und notwendige Runtime sauber eingebunden werden können.

Model weights nicht in die Application Binary packen, sofern sie nicht winzig sind.

---

# 40. DESKTOP UPDATE ARCHITEKTUR

Plane Updates vor.

Application Updates und Model Updates müssen getrennt sein.

Beispiel:

    VOLUM App:
        0.3.1

    TRELLIS.2:
        model version X

Das verhindert, dass ein App-Update automatisch riesige Model Downloads erzwingt.

---

# 41. GITHUB

Das Repository ist öffentlich.

Claude Code darf und soll das bestehende GitHub-Repository verwenden, da GitHub-Zugriff bereits verfügbar ist.

Arbeite mit:

    git

und überprüfe:

    remote
    branch
    status

Vor Änderungen:

    git status

Keine bestehenden Benutzeränderungen überschreiben.

Keine destruktiven Git-Kommandos verwenden, wenn dies nicht ausdrücklich erforderlich ist.

---

# 42. SEMANTIC VERSIONING

VOLUM folgt:

    Semantic Versioning 2.0.0

Format:

    MAJOR.MINOR.PATCH

Beispiele:

    0.1.0
    0.2.0
    0.2.1
    1.0.0

Bedeutung:

PATCH:
    rückwärtskompatibler Bugfix

MINOR:
    rückwärtskompatible Funktion

MAJOR:
    inkompatible Änderung

Während der frühen Entwicklung darf VOLUM 0.x verwenden.

---

# 43. VERSION SINGLE SOURCE OF TRUTH

Keine widersprüchlichen Versionen.

Definiere eine zentrale Version und synchronisiere:

- Python package
- frontend package
- Tauri package
- CLI
- application metadata
- Git tags

Wenn technisch sinnvoll, automatisiere diese Synchronisierung.

---

# 44. CONVENTIONAL COMMITS

Nutze:

    feat:
    fix:
    perf:
    refactor:
    test:
    docs:
    build:
    ci:
    chore:

Beispiele:

    feat: add multi-image reconstruction

    fix: handle missing texture references

    perf: reduce GLB viewer memory usage

---

# 45. CHANGELOG

Pflege:

    CHANGELOG.md

Nutze Releases nach Semantic Versioning.

Beispiel:

    ## [0.2.0]

    ### Added
    - Multi-image input
    - Hunyuan3D provider

    ### Fixed
    - ...

---

# 46. GITHUB ACTIONS

CI soll mindestens prüfen:

    frontend lint
    frontend tests
    backend tests
    type checking
    Python lint
    build
    package consistency

GPU-Tests nur auf geeigneten Runnern.

Nicht versuchen, riesige ML-Modelle bei jedem normalen CI-Lauf herunterzuladen.

---

# 47. RELEASES

Releases über GitHub Tags:

    v0.1.0
    v0.2.0
    v0.2.1

Später:

    macOS arm64
    Windows x64
    Linux x64

als Release Artifacts.

---

# 48. REPOSITORY-DOKUMENTATION

Mindestens:

    README.md
    CONTRIBUTING.md
    SECURITY.md
    LICENSE
    CHANGELOG.md

Dokumentation:

    docs/
        architecture.md
        research.md
        model-evaluation.md
        benchmarks.md
        privacy.md
        installation.md
        development.md
        troubleshooting.md
        models.md

README muss enthalten:

- Was ist VOLUM?
- Screenshots/GIF später vorbereiten
- Features
- unterstützte Plattformen
- Installation
- Hardwareanforderungen
- Model installation
- CLI
- Entwicklung
- Lizenz
- Contributing

---

# 49. OPEN SOURCE

Schreibe Code so, dass externe Contributors ihn verstehen können.

Keine privaten lokalen Pfade.

Keine persönlichen API Keys.

Keine persönlichen GitHub-Namen hardcoden.

Keine geheimen Konfigurationen committen.

Erstelle:

    .env.example

aber niemals:

    .env

mit Secrets committen.

---

# 50. SECURITY

Behandle Bilder und 3D-Dateien als untrusted input.

Implementiere:

- Dateigrößenlimits
- MIME/type validation
- sichere Dateinamen
- UUID storage paths
- path traversal protection
- subprocess safety
- timeout
- cancellation
- temporary directory cleanup
- sichere Archivbehandlung
- keine unsichere shell interpolation

---

# 51. PERFORMANCE

VOLUM soll große Assets möglichst effizient behandeln.

Achte auf:

- RAM
- Unified Memory
- VRAM
- Browser GPU memory
- Disk I/O
- Model loading time
- duplicate model loading

Modelle nach Möglichkeit:

    load once
    reuse

aber Ressourcen sauber freigeben können.

---

# 52. MEMORY MANAGEMENT

Besonders wichtig auf Apple Silicon:

Unified Memory bedeutet:

    CPU + GPU share memory

Deshalb:

- unnötige Kopien vermeiden
- große Tensoren früh freigeben
- Zwischenresultate löschen
- Cache kontrollieren
- Memory pressure erkennen

Wenn ein Modell nicht mehr genügend Speicher bekommt:

Verständliche Fehlermeldung.

---

# 53. APP LIFECYCLE

Beim Start:

    initialize
      ↓
    detect hardware
      ↓
    detect runtimes
      ↓
    detect installed models
      ↓
    ready

Die App soll nicht bereits beim Start alle großen Modelle laden.

Lazy loading bevorzugen.

---

# 54. MODEL LOADING

Modelle nur laden, wenn sie tatsächlich benötigt werden.

Nach Inference:

    retain

oder

    unload

abhängig von verfügbarer Hardware und Konfiguration.

---

# 55. ERROR HANDLING

Benutzerfehler und technische Fehler trennen.

Schlecht:

    RuntimeError: CUDA out of memory

Besser:

    VOLUM could not generate the model.

    The selected model requires more GPU memory
    than is currently available.

    Model:
        ...

    Available:
        ...

    Required:
        ...

    Try:
        - Fast mode
        - lower resolution
        - another model

Technische Exception trotzdem vollständig loggen.

---

# 56. NO HIDDEN CLOUD DEPENDENCIES

Bevorzuge lokale Bibliotheken.

Wenn eine Library zwingend eine externe API benötigt:

    dokumentieren

und prüfen, ob eine lokale Alternative existiert.

Keine externe AI API als versteckte Voraussetzung.

---

# 57. ARCHITECTURE FOR FUTURE FEATURES

Architektur vorbereiten für:

- Multi-view reconstruction
- camera pose estimation
- photogrammetry
- Gaussian Splatting
- NeRF
- texture regeneration
- retopology
- segmentation
- object editing
- text-guided editing
- rigging
- animation
- game-ready optimization

Aber:

Nicht alles implementieren.

V1 muss fokussiert bleiben.

---

# 58. WICHTIGE UNTERSCHEIDUNG

Unterscheide:

## GENERATION

Ein Bild → plausible 3D-Geometrie.

## RECONSTRUCTION

Mehrere Bilder → möglichst konsistente 3D-Geometrie.

VOLUM soll beide Konzepte unterstützen.

Die UI muss diese Unterscheidung nicht unnötig technisch machen, aber die interne Architektur muss sie berücksichtigen.

---

# 59. PRIORITÄTEN

## P0

- funktionierende lokale Desktop-App
- echter Image → 3D Workflow
- Apple Silicon Support
- echte Inference
- GLB
- 3D Viewer
- Hardware Detection
- Model Manager
- reproduzierbare lokale Jobs

## P1

- Multi-image
- zweites Modell
- Mesh validation
- optimization
- benchmarking
- CLI

## P2

- weitere Exportformate
- advanced editing
- zusätzliche Reconstruction-Verfahren

---

# 60. DEFINITION OF DONE — V1

VOLUM V1 ist erst fertig, wenn:

1. Tauri-App startet.
2. React UI funktioniert.
3. Python Engine funktioniert.
4. Hardware wird erkannt.
5. Model Manager funktioniert.
6. Nutzer kann ein Bild importieren.
7. Nutzer kann mehrere Bilder importieren.
8. Nutzer kann Generate starten.
9. echte Inference läuft.
10. Job-Status wird korrekt angezeigt.
11. GLB wird erzeugt.
12. GLB wird validiert.
13. GLB wird im Viewer dargestellt.
14. Nutzer kann GLB exportieren.
15. Projekt kann gespeichert werden.
16. Fehler werden verständlich angezeigt.
17. CLI funktioniert.
18. `volum doctor` funktioniert.
19. Tests funktionieren.
20. GitHub CI funktioniert.
21. SemVer ist eingerichtet.
22. CHANGELOG ist vorhanden.
23. README beschreibt Installation.
24. Modell-Lizenzen sind dokumentiert.
25. keine zentrale Funktion ist Fake oder Placeholder.

---

# 61. ENTWICKLUNGSPHASE 0 — BESTANDSAUFNAHME

Beginne NICHT mit der Implementierung.

Untersuche zuerst das bestehende Repository.

Führe sinngemäß aus:

    pwd
    git status
    git remote -v
    git branch
    ls
    find ...

Prüfe:

    package.json
    pyproject.toml
    README
    bestehende Apps
    Docker
    Tests
    Scripts
    GitHub configuration

**Bestehenden Code niemals ohne vorherige Analyse überschreiben.**

Wenn bereits ein Projekt vorhanden ist, integriere VOLUM sinnvoll in die bestehende Struktur.

---

# 62. ENTWICKLUNGSPHASE 1 — RESEARCH

Recherchiere zuerst den aktuellen Stand der relevanten Image-to-3D-Modelle.

Dokumentiere:

    docs/research.md
    docs/model-evaluation.md

Entscheide danach:

- Primary Provider
- Secondary Provider
- Fast Provider, falls sinnvoll
- Apple Silicon Runtime
- Windows/Linux Runtime

Begründe die Entscheidungen.

---

# 63. ENTWICKLUNGSPHASE 2 — ARCHITECTURE

Erstelle:

    docs/architecture.md

Dokumentiere:

- Tauri
- React
- Python
- IPC/API
- Model Provider
- Runtime abstraction
- Job system
- storage
- pipeline
- viewer
- packaging

Danach erst implementieren.

---

# 64. ENTWICKLUNGSPHASE 3 — FOUNDATION

Implementiere:

- Repository structure
- Tauri
- React
- Python engine
- configuration
- logging
- storage
- hardware detection
- doctor
- model registry
- tests

Danach:

    lint
    test
    build

---

# 65. ENTWICKLUNGSPHASE 4 — REAL MODEL

Integriere den ausgewählten Primary Provider.

Es muss echte Inference stattfinden.

Keine Fake-Ausgabe.

Wenn das Modell auf deinem M1 nicht vollständig ausführbar ist:

- prüfe MLX/MPS-Alternativen
- prüfe kompatible Implementierungen
- dokumentiere Einschränkungen
- implementiere Provider trotzdem sauber
- ermögliche einen echten Smoke-Test auf kompatibler Hardware

Nicht einfach aufgeben.

---

# 66. ENTWICKLUNGSPHASE 5 — PIPELINE

Implementiere:

    import
    analyze
    preprocess
    inference
    mesh
    texture
    optimize
    validate
    export

Jede Stage separat testen.

---

# 67. ENTWICKLUNGSPHASE 6 — DESKTOP UX

Implementiere die komplette UI:

    Home
    Projects
    Generate
    Processing
    Result
    Models
    Settings
    System/Doctor

Nicht unnötig viele Screens.

Der zentrale Workflow muss schnell verständlich sein.

---

# 68. ENTWICKLUNGSPHASE 7 — MULTI-MODEL

Integriere einen zweiten Provider.

Teste, ob die Abstraktion tatsächlich funktioniert.

Wenn Provider 2 nur durch Änderungen an zehn Stellen eingebaut werden kann:

    STOP

und Architektur verbessern.

---

# 69. ENTWICKLUNGSPHASE 8 — QUALITY

Implementiere:

- validation
- optimization
- benchmarks
- metadata
- reproducibility
- cache

---

# 70. ENTWICKLUNGSPHASE 9 — PACKAGING

Erzeuge:

    macOS arm64
    Windows x64
    Linux x64

jeweils soweit der aktuelle Stand der verwendeten ML-Provider dies realistisch erlaubt.

Dokumentiere Plattform-/Modell-Einschränkungen explizit.

---

# 71. CLAUDE-CODE-ARBEITSREGELN

Arbeite iterativ.

Vor jeder größeren Änderung:

1. Repository untersuchen.
2. relevante Dateien lesen.
3. Abhängigkeiten verstehen.
4. kleinsten sinnvollen Schritt definieren.
5. implementieren.
6. testen.
7. Ergebnis prüfen.
8. erst dann fortfahren.

Nutze vorhandene gute Implementierungen.

Keine unnötigen Komplett-Rewrites.

---

# 72. GIT-REGELN

Vor jeder Änderung:

    git status

Keine User-Änderungen zerstören.

Keine:

    git reset --hard

ohne ausdrückliche Notwendigkeit.

Keine Force Pushes.

Committe logisch getrennte Änderungen.

Nutze Conventional Commits.

Beispiele:

    feat: add local model manager
    feat: add trellis provider
    fix: prevent duplicate model loading
    test: add GLB validation tests
    docs: document Apple Silicon setup

---

# 73. RESEARCH WÄHREND DER ENTWICKLUNG

Der 3D-KI-Markt entwickelt sich schnell.

Wenn während der Entwicklung ein deutlich besseres Open-Source-Modell erscheint:

Prüfe es.

Wenn es objektiv besser geeignet ist:

    Architecture decision update

Dokumentiere:

    warum
    wann
    welche Version
    welche Lizenz
    welche Hardware
    welche Auswirkungen

Nicht aus Trägheit an einem schlechteren Modell festhalten.

---

# 74. CODE QUALITY

Bevorzuge:

- kleine Module
- klare Interfaces
- Type Safety
- Dependency Injection, wo sinnvoll
- explizite Fehler
- gute Typen
- keine globalen versteckten Zustände
- keine magischen Konstanten

Keine unnötige Abstraktion.

Aber die Model Provider und Runtime Layer müssen sauber abstrahiert sein.

---

# 75. FINALER ANSPRUCH

VOLUM soll kein schneller Prototyp sein, der nur auf einem Rechner funktioniert.

Es soll ein **echtes Open-Source-Desktop-Produkt** werden:

    macOS Apple Silicon
           +
    Windows
           +
    Linux

mit:

    local inference
    modular models
    clean architecture
    real 3D output
    reproducibility
    quality validation
    professional UX
    CLI
    GitHub CI
    Semantic Versioning

Der primäre Entwicklungsfall ist:

    Apple M1

Deshalb hat Apple Silicon bei der Entwicklung besondere Priorität.

Gleichzeitig darf keine zentrale Architekturentscheidung Windows/Linux unnötig ausschließen.

---

# 76. DEIN ERSTER AUFTRAG

Beginne jetzt ausschließlich mit:

## PHASE 0 — REPOSITORY AUDIT

und anschließend:

## PHASE 1 — CURRENT MODEL RESEARCH

Noch keine umfangreiche Implementierung.

Liefere danach eine konkrete technische Entscheidung:

    Recommended architecture
    Primary model
    Secondary model
    Apple runtime
    Windows runtime
    Linux runtime
    Packaging strategy
    Repository structure
    Key risks
    License risks
    Hardware risks

Erst wenn diese Grundlage geprüft ist, beginne mit der Implementierung.

Arbeite dabei selbstständig und gründlich.
