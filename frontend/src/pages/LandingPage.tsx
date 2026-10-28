import Brand from "../components/ui/Brand";
import Icon from "../components/ui/Icon";
import ThemeToggle from "../components/ui/ThemeToggle";
export default function LandingPage({
  onStart,
  dark,
  toggle,
}: {
  onStart: () => void;
  dark: boolean;
  toggle: () => void;
}) {
  return (
    <div className="landing">
      <header className="public-header">
        <Brand />
        <div className="row">
          <ThemeToggle dark={dark} toggle={toggle} />
          <button className="button primary small" onClick={onStart}>
            Get started <Icon name="arrow" size={16} />
          </button>
        </div>
      </header>
      <main>
        <section className="hero">
          <div className="hero-copy">
            <span className="eyebrow">
              <span className="status-dot" /> LESS ADMIN. MORE MOMENTUM.
            </span>
            <h1>
              Great meetings.
              <br />
              <span>
                Even better
                <br />
                follow-through.
              </span>
            </h1>
            <p>
              Turn the conversation into your next move. Ticket2Go captures your
              meetings, brings the important bits together, and helps you turn
              action items into approved tasks.
            </p>
            <button className="button primary" onClick={onStart}>
              Start your next meeting <Icon name="arrow" />
            </button>
            <div className="hero-note">
              Capture. Summarize. Review. Get going.
            </div>
          </div>
          <div className="preview-wrap">
            <div className="preview-label">
              <span className="status-dot" /> A LITTLE LESS “WHAT DID WE AGREE
              ON?”
            </div>
            <div className="preview-window">
              <div className="preview-top">
                <div className="window-dots">
                  <i />
                  <i />
                  <i />
                </div>
                <span>YOUR MEETING, MOVING FORWARD</span>
              </div>
              <div className="preview-body">
                <div className="row between">
                  <span className="eyebrow">PRODUCT TEAM</span>
                  <span className="badge approved">Completed</span>
                </div>
                <h2>Monday, with a plan.</h2>
                <p className="muted">Team catch-up · Example meeting</p>
                <div className="waveform" aria-hidden="true">
                  {Array.from({ length: 40 }, (_, i) => (
                    <i
                      key={i}
                      style={{ height: `${12 + ((i * 17) % 42)}px` }}
                    />
                  ))}
                </div>
                <div className="preview-summary">
                  <span className="mini-label">THE TAKEAWAY</span>
                  <p>
                    A clear direction for the next release, with the details
                    captured and next steps ready to review.
                  </p>
                </div>
                <div className="preview-task">
                  <span className="check-circle">
                    <Icon name="check" size={16} />
                  </span>
                  <div>
                    <strong>Share the updated launch plan</strong>
                    <span>Alex · Next Friday</span>
                  </div>
                  <span className="badge approved">Approved</span>
                </div>
                <div className="preview-task">
                  <span className="pending-circle" />
                  <div>
                    <strong>Review the onboarding flow</strong>
                    <span>Sam · Next week</span>
                  </div>
                  <span className="badge pending">Review</span>
                </div>
              </div>
            </div>
            <div className="floating-note">
              <span>✦</span> From “let's do it” to done.
            </div>
          </div>
        </section>
        <section className="how-section">
          <div className="section-heading">
            <span className="eyebrow">A SIMPLE WAY FORWARD</span>
            <h2>Your conversation has a next chapter.</h2>
            <p className="muted">
              Stay present in the meeting. Pick up the important details
              afterwards.
            </p>
          </div>
          <div className="feature-grid">
            {[
              {
                n: "01",
                icon: "mic" as const,
                title: "Be in the conversation",
                text: "Start a meeting and capture a live transcript. Explore your workspace while recording continues.",
              },
              {
                n: "02",
                icon: "history" as const,
                title: "Keep the clarity",
                text: "Revisit your transcript, key decisions, and AI summary in one organized meeting history.",
              },
              {
                n: "03",
                icon: "tasks" as const,
                title: "Make the next move",
                text: "Review suggested tasks. Edit the details, approve what fits, and reject what doesn't.",
              },
            ].map((feature) => (
              <article className="feature" key={feature.n}>
                <div className="row between">
                  <span className="feature-icon">
                    <Icon name={feature.icon} />
                  </span>
                  <span className="feature-number">{feature.n}</span>
                </div>
                <h3>{feature.title}</h3>
                <p>{feature.text}</p>
              </article>
            ))}
          </div>
        </section>
        <section className="bottom-cta">
          <div>
            <span className="eyebrow">MAKE ROOM FOR THE REAL WORK</span>
            <h2>Good conversations deserve action.</h2>
          </div>
          <button className="button primary" onClick={onStart}>
            Let's get going <Icon name="arrow" />
          </button>
        </section>
      </main>
      <footer>
        <Brand />
        <span>A little clarity. A lot of possibility.</span>
        <span>Ticket2Go © {new Date().getFullYear()}</span>
      </footer>
    </div>
  );
}
