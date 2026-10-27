import Icon from "./Icon";
export default function Brand() {
  return (
    <span className="brand">
      <span className="brand-mark">
        <Icon name="tasks" size={19} />
      </span>
      Ticket<span className="brand-accent">2</span>Go
      <span className="brand-dot">.</span>
    </span>
  );
}
