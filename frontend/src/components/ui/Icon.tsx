type Name =
  | "bot"
  | "arrow"
  | "mic"
  | "grid"
  | "history"
  | "tasks"
  | "sun"
  | "moon"
  | "check"
  | "close"
  | "edit"
  | "logout";
const paths: Record<Name, string> = {
  bot: "M12 3v3m-2-3h4M5 7h14v13H5ZM2 11v5m20-5v5M9 11v2m6-2v2m-6 4h6",
  arrow: "M5 12h14m-6-6 6 6-6 6",
  mic: "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3ZM5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8",
  grid: "M3 3h7v7H3ZM14 3h7v7h-7ZM3 14h7v7H3ZM14 14h7v7h-7Z",
  history: "M3 4v5h5M3 9a9 9 0 1 1 0 6M12 7v5l3 2",
  tasks: "m3 6 2 2 3-4m3 2h10M3 13l2 2 3-4m3 2h10M3 20l2 2 3-4m3 2h10",
  sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1",
  moon: "M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11Z",
  check: "m5 12 4 4L19 6",
  close: "m6 6 12 12M6 18 18 6",
  edit: "m14 5 5 5M4 20l5-1L21 7l-5-5L4 14Z",
  logout: "M9 4H4v16h5m5-13 5 5-5 5m-6-5h13",
};
export default function Icon({
  name,
  size = 20,
}: {
  name: Name;
  size?: number;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true">
      <path d={paths[name]} />
    </svg>
  );
}
