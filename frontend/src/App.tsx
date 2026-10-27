import { useState } from "react";
import { useTheme } from "./hooks/useTheme";
import LandingPage from "./pages/LandingPage";
import LoginPage from "./pages/LoginPage";
import Workspace from "./components/layout/Workspace";

export default function App() {
  const [page, setPage] = useState<"landing" | "login" | "workspace">(
    "landing",
  );
  const theme = useTheme();
  if (page === "workspace")
    return <Workspace {...theme} onLogout={() => setPage("landing")} />;
  if (page === "login")
    return (
      <LoginPage
        {...theme}
        onLogin={() => setPage("workspace")}
        onBack={() => setPage("landing")}
      />
    );
  return <LandingPage {...theme} onStart={() => setPage("login")} />;
}
