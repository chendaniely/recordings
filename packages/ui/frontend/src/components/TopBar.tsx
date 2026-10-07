import { ThemeSwitch } from "./ThemeSwitch";

export function TopBar() {
  return (
    <header className="topbar">
      <span className="logo"><i aria-hidden />Recordings</span>
      <nav><span className="nav on">Library</span></nav>
      <ThemeSwitch />
    </header>
  );
}
