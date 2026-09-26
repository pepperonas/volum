import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { Bounds, Grid, OrbitControls, useGLTF } from "@react-three/drei";
import * as THREE from "three";
import { Button, Spinner, cx } from "./ui";

export type ViewMode = "material" | "clay" | "wireframe";

/**
 * The lighting is written out rather than loaded from an environment map.
 *
 * drei's `<Environment preset=…>` fetches an HDRI from a CDN — a quiet network
 * request in an application whose whole claim is that nothing leaves the
 * machine. Three explicit lights cost nothing and keep that claim true.
 */
function Lighting() {
  return (
    <>
      <hemisphereLight args={["#c9d4f5", "#20242e", 0.6]} />
      <directionalLight position={[4, 6, 4]} intensity={2.1} castShadow={false} />
      <directionalLight position={[-5, 2, -3]} intensity={0.7} color="#93a7e0" />
      <directionalLight position={[0, -4, 2]} intensity={0.35} color="#ffd9b0" />
    </>
  );
}

/**
 * A neutral surface for looking at *shape*.
 *
 * A printed model has no colour, and a generated one often carries a texture
 * that flatters the geometry. Clay is the honest default for deciding whether
 * a mesh is any good.
 */
const CLAY = new THREE.MeshStandardMaterial({
  color: "#c8ccd6",
  roughness: 0.62,
  metalness: 0.0,
  flatShading: false,
});

const WIREFRAME = new THREE.MeshBasicMaterial({
  color: "#b3c5ff",
  wireframe: true,
  transparent: true,
  opacity: 0.55,
});

/**
 * The fill under the wireframe.
 *
 * A bare wireframe of an 83,000-triangle mesh is a filled blob at any zoom
 * that shows the whole object — the lines sit closer together than the pixels.
 * Filling behind it and drawing the mesh on top is what every modelling tool
 * does, and it is the only way the mode says anything at all. The polygon
 * offset stops the fill fighting the lines for the same depth.
 */
const WIREFRAME_FILL = new THREE.MeshStandardMaterial({
  color: "#2a3040",
  roughness: 0.9,
  metalness: 0,
  polygonOffset: true,
  polygonOffsetFactor: 1,
  polygonOffsetUnits: 1,
});

function Asset({ url, mode }: { url: string; mode: ViewMode }) {
  const { scene } = useGLTF(url);

  // One copy per viewer instance: useGLTF caches by URL, and mutating the
  // cached scene's materials would follow the model into the next mount.
  const object = useMemo(() => scene.clone(true), [scene]);

  // A second copy carries the wireframe over the first. Cloning is cheap —
  // three.js shares the geometry by reference — and it saves having to make
  // one material do two jobs.
  const overlay = useMemo(() => (mode === "wireframe" ? scene.clone(true) : null), [scene, mode]);

  useEffect(() => {
    // three.js types a traversed mesh with `any` generics, so the material is
    // pinned down once here rather than spread through the restore path.
    type Restorable = THREE.Material | THREE.Material[];
    const originals = new Map<THREE.Object3D, Restorable>();
    object.traverse((node) => {
      if (!(node instanceof THREE.Mesh)) return;
      originals.set(node, node.material as Restorable);
      if (mode === "clay") node.material = CLAY;
      else if (mode === "wireframe") node.material = WIREFRAME_FILL;
    });
    return () => {
      for (const [node, material] of originals) {
        if (node instanceof THREE.Mesh) node.material = material;
      }
    };
  }, [object, mode]);

  useEffect(() => {
    overlay?.traverse((node) => {
      if (node instanceof THREE.Mesh) node.material = WIREFRAME;
    });
  }, [overlay]);

  return (
    <>
      <primitive object={object} />
      {overlay && <primitive object={overlay} />}
    </>
  );
}

/** Frame the object once it is in the scene, whatever scale it came at. */
function Framed({ children }: { children: React.ReactNode }) {
  return (
    <Bounds fit clip observe margin={1.25}>
      {children}
    </Bounds>
  );
}

function Resetter({ signal }: { signal: number }) {
  const controls = useThree((state) => state.controls);
  useEffect(() => {
    // Not every controls implementation has a reset; OrbitControls does.
    const resettable = controls as { reset?: () => void } | null;
    resettable?.reset?.();
  }, [signal, controls]);
  return null;
}

export function Viewer({
  url,
  className,
}: {
  url: string;
  className?: string;
}) {
  const [mode, setMode] = useState<ViewMode>("clay");
  const [showGrid, setShowGrid] = useState(true);
  const [resetSignal, setResetSignal] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);

  // The loader keeps parsed documents keyed by URL. Each job gets a fresh blob
  // URL, so without this every model viewed stays in memory for the session.
  useEffect(() => () => useGLTF.clear(url), [url]);

  const modes: { value: ViewMode; label: string }[] = [
    { value: "clay", label: "Clay" },
    { value: "material", label: "Material" },
    { value: "wireframe", label: "Wireframe" },
  ];

  return (
    <div
      ref={containerRef}
      className={cx(
        "relative overflow-hidden rounded-[var(--radius-card)] border border-hairline bg-[#0e1219]",
        className,
      )}
    >
      <Canvas
        camera={{ position: [2.2, 1.6, 2.6], fov: 42, near: 0.01, far: 200 }}
        dpr={[1, 2]}
        gl={{ antialias: true, preserveDrawingBuffer: false }}
      >
        <color attach="background" args={["#0e1219"]} />
        <Lighting />
        <Suspense fallback={null}>
          <Framed>
            <Asset url={url} mode={mode} />
          </Framed>
        </Suspense>
        {showGrid && (
          <Grid
            infiniteGrid
            cellSize={0.1}
            sectionSize={1}
            cellColor="#242b38"
            sectionColor="#323b4d"
            fadeDistance={22}
            fadeStrength={1.4}
            position={[0, -0.001, 0]}
          />
        )}
        <OrbitControls makeDefault enableDamping dampingFactor={0.08} />
        <Resetter signal={resetSignal} />
      </Canvas>

      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between gap-3 p-3">
        <div className="pointer-events-auto flex gap-1 rounded-md border border-hairline bg-[color-mix(in_srgb,var(--color-surface)_88%,transparent)] p-0.5 backdrop-blur-sm">
          {modes.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => setMode(option.value)}
              className={cx(
                "rounded px-2.5 py-1 text-[12px] transition-colors",
                mode === option.value
                  ? "bg-raised text-ink"
                  : "text-ink-muted hover:text-ink",
              )}
            >
              {option.label}
            </button>
          ))}
        </div>

        <div className="pointer-events-auto flex gap-1.5">
          <Button
            tone="quiet"
            className="border border-hairline bg-[color-mix(in_srgb,var(--color-surface)_88%,transparent)] px-2 py-1 text-[12px] backdrop-blur-sm"
            onClick={() => setShowGrid((current) => !current)}
          >
            {showGrid ? "Hide grid" : "Show grid"}
          </Button>
          <Button
            tone="quiet"
            className="border border-hairline bg-[color-mix(in_srgb,var(--color-surface)_88%,transparent)] px-2 py-1 text-[12px] backdrop-blur-sm"
            onClick={() => setResetSignal((n) => n + 1)}
          >
            Reset view
          </Button>
        </div>
      </div>

      <p className="pointer-events-none absolute inset-x-0 bottom-0 p-3 text-center text-[11px] text-ink-faint">
        Drag to orbit · scroll to zoom · right-drag to pan
      </p>
    </div>
  );
}

export function ViewerLoading({ className }: { className?: string }) {
  return (
    <div
      className={cx(
        "flex items-center justify-center gap-2 rounded-[var(--radius-card)] border border-hairline bg-[#0e1219] text-[13px] text-ink-muted",
        className,
      )}
    >
      <Spinner /> Loading the model…
    </div>
  );
}
