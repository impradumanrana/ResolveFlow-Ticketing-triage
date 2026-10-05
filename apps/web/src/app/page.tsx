// Rendered per request so the C13 content-security-policy nonce reaches the
// framework's bootstrap scripts; a prerendered page has none, and
// `strict-dynamic` makes `'self'` inoperative. See lib/security/headers.ts.
export const dynamic = "force-dynamic";

const navItems = [
  { label: "Overview", active: true },
  { label: "Unified inbox", active: false },
  { label: "Support knowledge", active: false },
  { label: "Quality", active: false },
  { label: "Mailboxes", active: false },
];

const foundationItems = [
  {
    number: "01",
    title: "Production web foundation",
    detail: "Next.js App Router with strict TypeScript and accessible design tokens.",
    status: "Ready",
  },
  {
    number: "02",
    title: "Private AI service contract",
    detail: "Versioned API boundary protects the proven Python triage workflow.",
    status: "Ready",
  },
  {
    number: "03",
    title: "Client identity and data",
    detail: "Workspace authentication and durable records arrive in C03–C04.",
    status: "Planned",
  },
];

function ResolveFlowMark() {
  return (
    <span className="brand-mark" aria-hidden="true">
      <span />
    </span>
  );
}

function Icon({ name }: { name: "grid" | "inbox" | "book" | "shield" | "mail" }) {
  const paths = {
    grid: <path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z" />,
    inbox: <path d="M4 5h16v12H4zM4 13h4l2 3h4l2-3h4" />,
    book: <path d="M5 4h10a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3zM8 4v16" />,
    shield: <path d="M12 3l8 3v6c0 5-3.4 8-8 9-4.6-1-8-4-8-9V6zM9 12l2 2 4-5" />,
    mail: <path d="M3 5h18v14H3zM3 7l9 7 9-7" />,
  };
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="nav-icon">
      {paths[name]}
    </svg>
  );
}

const icons = ["grid", "inbox", "book", "shield", "mail"] as const;

export default function Home() {
  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <ResolveFlowMark />
          <span>ResolveFlow</span>
        </div>
        <p className="eyebrow sidebar-eyebrow">Support operations</p>

        <nav aria-label="Primary navigation">
          <ul className="nav-list">
            {navItems.map((item, index) => (
              <li key={item.label}>
                <button
                  className={item.active ? "nav-item active" : "nav-item"}
                  type="button"
                  aria-current={item.active ? "page" : undefined}
                  disabled={!item.active}
                  title={!item.active ? "Available in a later client phase" : undefined}
                >
                  <Icon name={icons[index]} />
                  <span>{item.label}</span>
                  {!item.active && <span className="soon">Soon</span>}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <div className="sidebar-status">
          <p className="eyebrow">Environment</p>
          <div className="status-line">
            <span className="status-dot" />
            <span>Foundation preview</span>
          </div>
          <p>No mailbox or customer data is connected.</p>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="mobile-brand"><ResolveFlowMark /> ResolveFlow</p>
            <p className="breadcrumb">Client workspace <span>/</span> Overview</p>
          </div>
          <div className="topbar-actions">
            <span className="safe-chip"><span /> Observe mode</span>
            <button className="avatar" type="button" aria-label="Account settings" disabled>RF</button>
          </div>
        </header>

        <div className="content">
          <section className="hero" aria-labelledby="page-title">
            <div>
              <p className="eyebrow hero-eyebrow">Client production foundation</p>
              <h1 id="page-title">Every support signal,<br />one calm workspace.</h1>
              <p className="hero-copy">
                ResolveFlow brings mailboxes, grounded knowledge, safe AI triage, and human decisions
                together—without sending anything automatically.
              </p>
            </div>
            <div className="hero-aside">
              <span className="hero-number">C01</span>
              <span>Foundation phase</span>
            </div>
          </section>

          <section className="notice" aria-label="Foundation status">
            <span className="notice-icon"><Icon name="shield" /></span>
            <div>
              <strong>Safe by construction</strong>
              <p>This preview uses synthetic content only. Authentication, mailboxes, and client data remain disconnected.</p>
            </div>
            <span className="notice-badge">No auto-send</span>
          </section>

          <div className="section-heading">
            <div>
              <p className="eyebrow">Build readiness</p>
              <h2>Foundation checkpoints</h2>
            </div>
            <span className="progress-label">2 of 3 prepared</span>
          </div>

          <section className="foundation-grid" aria-label="Foundation checkpoints">
            {foundationItems.map((item) => (
              <article className="foundation-card" key={item.number}>
                <div className="card-topline">
                  <span className="card-number">{item.number}</span>
                  <span className={item.status === "Ready" ? "card-status ready" : "card-status"}>
                    {item.status}
                  </span>
                </div>
                <h3>{item.title}</h3>
                <p>{item.detail}</p>
              </article>
            ))}
          </section>

          <section className="empty-state" aria-labelledby="inbox-heading">
            <div className="empty-icon"><Icon name="inbox" /></div>
            <div>
              <p className="eyebrow">Unified inbox</p>
              <h2 id="inbox-heading">Ready for the client data layer</h2>
              <p>Mailbox connections begin only after identity, permissions, and durable records are in place.</p>
            </div>
            <button className="secondary-button" type="button" disabled>Connect mailbox in C06</button>
          </section>
        </div>
      </section>
    </main>
  );
}
