import Icon from "./Icon";
export default function EmptyState({
  title,
  description,
  onStart,
}: {
  title: string;
  description: string;
  onStart?: () => void;
}) {
  return (
    <div className="empty-state">
      <span className="feature-icon">
        <Icon name="tasks" size={25} />
      </span>
      <h3>{title}</h3>
      <p>{description}</p>
      {onStart && (
        <button className="button secondary" onClick={onStart}>
          Go to meeting <Icon name="arrow" size={17} />
        </button>
      )}
    </div>
  );
}
