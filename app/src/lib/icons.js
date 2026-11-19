/**
 * Inline SVG path data for the stat chips.
 *
 * Inlined rather than pulled from an icon package: the app is served over a
 * LAN with no route out, and a webfont or sprite sheet that 404s leaves
 * empty boxes on every card. These are a few hundred bytes in the bundle.
 *
 * Stroke, size and color all come from `.chip svg` in app.css, so each
 * entry is geometry only.
 */
export const ICONS = {
  humidity: '<path d="M8 1.6c3 3.4 4.8 5.8 4.8 8A4.8 4.8 0 0 1 3.2 9.6c0-2.2 1.8-4.6 4.8-8Z"/>',
  temp: '<path d="M9.6 9.1V3a1.6 1.6 0 0 0-3.2 0v6.1a3 3 0 1 0 3.2 0Z"/>',
  light:
    '<circle cx="8" cy="8" r="3"/><path d="M8 1v1.6M8 13.4V15M1 8h1.6M13.4 8H15M3 3l1.2 1.2M11.8 11.8 13 13M13 3l-1.2 1.2M4.2 11.8 3 13"/>',
  fan: '<circle cx="8" cy="8" r="1.7"/><path d="M8 6.3c0-2.6-.8-4.1-2.6-4.9 2.3-1 4.2-.4 5.1 1.8M9.4 8.9c2.2 1.3 3.9 1.5 5.5.5-.4 2.4-1.9 3.7-4.2 3.3M6.6 8.9c-2.2 1.3-3.1 2.7-2.9 4.6-1.9-1.5-2.2-3.4-.9-5.2"/>',
  clock: '<circle cx="8" cy="8" r="6.4"/><path d="M8 4.2V8l2.6 1.6"/>',
  uptime: '<path d="M1.6 8a6.4 6.4 0 1 1 1.9 4.5"/><path d="M1.6 12.8V9.2h3.6"/>',
  wifi: '<path d="M1.8 6.1a9 9 0 0 1 12.4 0M4.3 8.7a5.4 5.4 0 0 1 7.4 0"/><circle cx="8" cy="11.9" r="1"/>',
  build: '<path d="M8 1.4 14 4.7v6.6L8 14.6 2 11.3V4.7Z"/><path d="M2 4.7 8 8l6-3.3M8 8v6.6"/>',
  link: '<path d="M6.6 9.4a2.8 2.8 0 0 0 4 0l2-2a2.8 2.8 0 1 0-4-4l-1 1"/><path d="M9.4 6.6a2.8 2.8 0 0 0-4 0l-2 2a2.8 2.8 0 1 0 4 4l1-1"/>',
  gauge: '<path d="M2.4 12a6.4 6.4 0 1 1 11.2 0"/><path d="M8 12 10.8 7"/>',
  sliders:
    '<path d="M2 4.6h5M11 4.6h3M2 11.4h3M7 11.4h7"/><circle cx="9" cy="4.6" r="1.9"/><circle cx="5" cy="11.4" r="1.9"/>',
  pulse: '<path d="M1.4 8h3l2-4.6 3 9.2 2-4.6h3"/>',
  drop: '<path d="M8 1.6c3 3.4 4.8 5.8 4.8 8A4.8 4.8 0 0 1 3.2 9.6c0-2.2 1.8-4.6 4.8-8Z"/>',
  power:
    '<path d="M8 1.8v5.4"/><path d="M12.2 3.9a5.9 5.9 0 1 1-8.4 0"/>',
  chart:
    '<path d="M2 2v12h12"/><path d="M4.6 10.8 7 7.4l2.3 2 3.1-4.6"/>',
  help: '<circle cx="8" cy="8" r="6.4"/><path d="M6.1 6.2a2 2 0 0 1 3.9.6c0 1.3-2 1.7-2 3"/><circle cx="8" cy="11.7" r=".35"/>',
  gear:
    '<circle cx="8" cy="8" r="2"/><path d="M6.9 1.8h2.2l.35 1.75 1.25.7 1.7-.55 1.1 1.9-1.35 1.2v1.4l1.35 1.2-1.1 1.9-1.7-.55-1.25.7-.35 1.75H6.9l-.35-1.75-1.25-.7-1.7.55-1.1-1.9L3.85 8.7V7.3L2.5 6.1l1.1-1.9 1.7.55 1.25-.7Z"/>',
  expand:
    '<path d="M9.6 2.4H13.6V6.4"/><path d="M6.4 13.6H2.4V9.6"/><path d="M13.6 2.4 9 7M2.4 13.6 7 9"/>'
};
