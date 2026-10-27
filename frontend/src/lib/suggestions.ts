import type { Task } from "./types";
import { PRIORITY_OPTIONS } from "./types";

// Read only the action-item section; never invent tasks from other summary text.
export function parseSuggestions(summary: string): Task[] {
  const section = summary
    .split(/^#{1,3}\s+/m)
    .find((block) => /^.*Action Items[^\n]*\n/i.test(block));
  if (!section) return [];
  const clean = (text: string) => text.replace(/\*\*/g, "").trim();
  const tasks: Task[] = [];
  for (const raw of section.split("\n")) {
    const line = clean(raw.replace(/^\s*[-*\d.)]+\s*/, ""));
    const task = line.match(/^Task:\s*(.+)/i);
    if (task)
      tasks.push({
        id: crypto.randomUUID(),
        title: task[1],
        owner: "Not specified",
        priority: "Not specified",
        status: "pending",
      });
    else if (tasks.length && /^Owner:/i.test(line))
      tasks[tasks.length - 1].owner = line.replace(/^Owner:\s*/i, "");
    else if (tasks.length && /^Priority:/i.test(line)) {
      const priority = line.replace(/^Priority:\s*/i, "").trim();
      tasks[tasks.length - 1].priority =
        PRIORITY_OPTIONS.find(
          (option) => option.toLowerCase() === priority.toLowerCase(),
        ) ?? "Not specified";
    }
  }
  return tasks;
}
