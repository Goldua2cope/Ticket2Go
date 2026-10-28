import ReactMarkdown from "react-markdown";
export default function MeetingSummary({ summary }: { summary: string }) {
  return (
    <section className="panel">
      <div className="panel-heading">
        <h2>Meeting summary</h2>
        <span className="badge neutral">AI summary</span>
      </div>
      <div className="markdown">
        <ReactMarkdown>{summary}</ReactMarkdown>
      </div>
    </section>
  );
}
