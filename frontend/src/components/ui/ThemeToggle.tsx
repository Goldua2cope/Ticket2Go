import Icon from "./Icon";
export default function ThemeToggle({
  dark,
  toggle,
}: {
  dark: boolean;
  toggle: () => void;
}) {
  return (
    <button
      className="icon-button"
      onClick={toggle}
      aria-label={`Switch to ${dark ? "light" : "dark"} mode`}>
      <Icon name={dark ? "sun" : "moon"} />
    </button>
  );
}
