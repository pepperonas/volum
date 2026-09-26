/**
 * The window's icons.
 *
 * Drawn as outlines — `fill="none"` with a `currentColor` stroke — rather than
 * filled shapes with cut-outs. On a tool this size that reads better at 18px,
 * and it sidesteps the fill-rule trap that filled glyphs bring with them.
 */
import type { SVGProps } from "react";

function Glyph({ children, ...rest }: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="1em"
      height="1em"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  );
}

/** The application mark: an isometric cube, the same one as the app icon. */
export function CubeIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M12 3.2 20 7.6v8.8L12 20.8 4 16.4V7.6z" />
      <path d="M12 12.2 20 7.6M12 12.2 4 7.6M12 12.2v8.6" />
    </Glyph>
  );
}

export function GenerateIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <rect x="3" y="4" width="8.5" height="8.5" rx="1.5" />
      <path d="m5.2 12.5 2.1-2.6 1.8 2.1" />
      <path d="M14.5 19.5h6M17.5 16.5v6" />
      <path d="M13 8.2h5.4M16.2 5.6l2.6 2.6-2.6 2.6" />
    </Glyph>
  );
}

export function LibraryIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <rect x="3.2" y="4.5" width="17.6" height="15" rx="2" />
      <path d="M3.2 9.2h17.6M9 9.2v10.3" />
    </Glyph>
  );
}

export function ModelsIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M12 3.4 20 7.7v8.6L12 20.6 4 16.3V7.7z" />
      <path d="M12 12v8.6M12 12 4 7.7M12 12l8-4.3" />
      <circle cx="12" cy="12" r="1.7" />
    </Glyph>
  );
}

export function SystemIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <rect x="5" y="5" width="14" height="14" rx="2.2" />
      <rect x="9" y="9" width="6" height="6" rx="1" />
      <path d="M9 2.8v2.2M15 2.8v2.2M9 19v2.2M15 19v2.2M2.8 9H5M2.8 15H5M19 9h2.2M19 15h2.2" />
    </Glyph>
  );
}

export function SettingsIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2.8v2.6M12 18.6v2.6M21.2 12h-2.6M5.4 12H2.8M18.5 5.5l-1.8 1.8M7.3 16.7l-1.8 1.8M18.5 18.5l-1.8-1.8M7.3 7.3 5.5 5.5" />
    </Glyph>
  );
}

export function PrinterIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M7 9V4.2h10V9" />
      <rect x="3.5" y="9" width="17" height="7" rx="1.8" />
      <path d="M7 13.5h10v6.3H7z" />
    </Glyph>
  );
}

export function DownloadIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M12 3.6v11.2M7.8 10.6 12 14.8l4.2-4.2" />
      <path d="M4.5 18.2v1.4a.8.8 0 0 0 .8.8h13.4a.8.8 0 0 0 .8-.8v-1.4" />
    </Glyph>
  );
}

export function TrashIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M4.5 6.5h15M9.5 6.5V4.8a.8.8 0 0 1 .8-.8h3.4a.8.8 0 0 1 .8.8v1.7" />
      <path d="M6.6 6.5 7.4 19a1 1 0 0 0 1 .9h7.2a1 1 0 0 0 1-.9l.8-12.5" />
    </Glyph>
  );
}

export function StopIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <rect x="6.5" y="6.5" width="11" height="11" rx="1.6" />
    </Glyph>
  );
}

export function CheckIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="m5 12.6 4.6 4.6L19 7.6" />
    </Glyph>
  );
}

export function AlertIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M12 4.4 21 19.6H3z" />
      <path d="M12 10v4.1M12 17.1h.01" />
    </Glyph>
  );
}

export function FolderIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M3.4 18.4V6.2a1 1 0 0 1 1-1h4.3l2 2.4h8.9a1 1 0 0 1 1 1v9.8a1 1 0 0 1-1 1H4.4a1 1 0 0 1-1-1z" />
    </Glyph>
  );
}
