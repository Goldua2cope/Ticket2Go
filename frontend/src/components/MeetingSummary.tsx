import ReactMarkdown from "react-markdown";

type MeetingSummaryProps = {
  summary: string;
};

export default function MeetingSummary({ summary }: MeetingSummaryProps) {
  return (
    <section
      className="space-y-2 rounded border border-gray-300 p-4"
      aria-live="polite">
      <h2 className="text-lg font-semibold">Meeting summary</h2>
      <ReactMarkdown
        components={{
          h1: ({ children }) => (
            <h1 className="text-2xl font-bold">{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 className="mb-3 mt-6 text-xl font-bold">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-lg font-semibold">{children}</h3>
          ),
          p: ({ children }) => (
            <p className="mb-3 leading-relaxed">{children}</p>
          ),
          li: ({ children }) => (
            <li className="ml-4 list-disc text-gray-700">{children}</li>
          ),
          ul: ({ children }) => (
            <ul className="mb-4 list-disc space-y-2 pl-6">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="mb-4 list-decimal space-y-2 pl-6">{children}</ol>
          ),
        }}>
        {summary}
      </ReactMarkdown>
    </section>
  );
}
