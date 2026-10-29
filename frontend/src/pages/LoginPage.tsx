import { useState } from "react";
import Brand from "../components/ui/Brand";
import Icon from "../components/ui/Icon";
import ThemeToggle from "../components/ui/ThemeToggle";
export default function LoginPage({
  onLogin,
  onBack,
  dark,
  toggle,
}: {
  onLogin: () => void;
  onBack: () => void;
  dark: boolean;
  toggle: () => void;
}) {
  const [username, setUsername] = useState("demo");
  const [password, setPassword] = useState("ticket2go");
  const [error, setError] = useState("");
  return (
    <div className="login-page">
      <header className="public-header">
        <button type="button" className="brand-button" onClick={onBack} aria-label="Ticket2Go home">
          <Brand />
        </button>
        <ThemeToggle dark={dark} toggle={toggle} />
      </header>
      <main className="login-main">
        <div className="login-intro">
          <span className="eyebrow">YOUR NEXT MOVE STARTS HERE</span>
          <h1>
            Welcome to
            <br />
            <span>a clearer workday.</span>
          </h1>
          <p>
            One place for the conversation,
            <br />
            the decisions, and everything that comes next.
          </p>
        </div>
        <section className="login-card">
          <span className="feature-icon">
            <Icon name="tasks" />
          </span>
          <h2>Good to see you.</h2>
          <p className="muted">Sign in to your Ticket2Go workspace.</p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (username === "demo" && password === "ticket2go") onLogin();
              else
                setError(
                  "That username or password isn't right. Use the demo details below.",
                );
            }}>
            <label>
              Username
              <input
                autoComplete="username"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Your username"
              />
            </label>
            <label>
              Password
              <input
                autoComplete="current-password"
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Your password"
              />
            </label>
            {error && (
              <p role="alert" className="error">
                {error}
              </p>
            )}
            <button className="button primary full" type="submit">
              Enter workspace <Icon name="arrow" />
            </button>
          </form>

          <button className="text-button" onClick={onBack}>
            ← Back to home
          </button>
        </section>
      </main>
    </div>
  );
}
