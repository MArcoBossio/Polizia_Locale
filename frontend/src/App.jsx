import { useCallback, useEffect, useMemo, useState } from "react";
import "@/App.css";

const BACKEND_URL = (import.meta.env.VITE_BACKEND_URL || "").replace(/\/$/, "");
const API = BACKEND_URL ? `${BACKEND_URL}/api` : "/api";

function formatDate(value) {
  if (!value) return "-";
  try {
    return new Intl.DateTimeFormat("it-IT", {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(value));
  } catch {
    return value;
  }
}

function App() {
  const [files, setFiles] = useState([]);
  const [selectedSlug, setSelectedSlug] = useState("");
  const [current, setCurrent] = useState(null);
  const [loadingFiles, setLoadingFiles] = useState(true);
  const [loadingRows, setLoadingRows] = useState(false);
  const [error, setError] = useState("");
  const [regions, setRegions] = useState([]);
  const [selectedRegion, setSelectedRegion] = useState("");
  const [includeComunePec, setIncludeComunePec] = useState(false);
  const [webSearch, setWebSearch] = useState(true);
  const [pmSource, setPmSource] = useState(true);
  const [strict, setStrict] = useState(true);
  const [scrapeLimit, setScrapeLimit] = useState("");
  const [job, setJob] = useState(null);
  const [jobBusy, setJobBusy] = useState(false);
  const [apiKey, setApiKey] = useState(localStorage.getItem("POLIZIA_DASH_API_KEY") || "");
  const [query, setQuery] = useState("");
  const [sourceFilter, setSourceFilter] = useState("all");

  const refreshFiles = useCallback(async (preferredSlug = "") => {
    setLoadingFiles(true);
    setError("");
    try {
      const response = await fetch(`${API}/outputs`);
      if (!response.ok) {
        throw new Error(`Impossibile leggere gli output (${response.status})`);
      }
      const payload = await response.json();
      const nextFiles = payload.files || [];
      setFiles(nextFiles);
      setSelectedSlug((prev) => {
        if (preferredSlug && nextFiles.some((item) => item.slug === preferredSlug)) {
          return preferredSlug;
        }
        if (prev && nextFiles.some((item) => item.slug === prev)) {
          return prev;
        }
        return nextFiles[0]?.slug || "";
      });
    } catch (err) {
      setError(err.message || "Errore nel caricamento degli output");
    } finally {
      setLoadingFiles(false);
    }
  }, []);

  const refreshRegions = useCallback(async () => {
    try {
      const response = await fetch(`${API}/regions`);
      if (!response.ok) {
        throw new Error(`Impossibile leggere le regioni (${response.status})`);
      }
      const payload = await response.json();
      const nextRegions = payload.regions || [];
      setRegions(nextRegions);
      setSelectedRegion((prev) => prev || nextRegions[0]?.name || "");
    } catch (err) {
      setError(err.message || "Errore nel caricamento delle regioni");
    }
  }, []);

  useEffect(() => {
    refreshFiles();
    refreshRegions();
  }, [refreshFiles, refreshRegions]);

  useEffect(() => {
    if (!selectedSlug) return;
    let cancelled = false;
    async function loadRows() {
      setLoadingRows(true);
      setError("");
      try {
        const response = await fetch(`${API}/outputs/${selectedSlug}`);
        if (!response.ok) {
          throw new Error(`Impossibile caricare ${selectedSlug} (${response.status})`);
        }
        const payload = await response.json();
        if (!cancelled) {
          setCurrent(payload);
          setSourceFilter("all");
          setQuery("");
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message || "Errore nel caricamento del report");
        }
      } finally {
        if (!cancelled) {
          setLoadingRows(false);
        }
      }
    }
    loadRows();
    return () => {
      cancelled = true;
    };
  }, [selectedSlug]);

  useEffect(() => {
    if (!job?.id || job.status === "completed" || job.status === "failed") {
      return undefined;
    }
    let cancelled = false;
    const interval = setInterval(async () => {
      try {
        const headers = { "Content-Type": "application/json" };
        if (apiKey) headers["x-api-key"] = apiKey;
        const response = await fetch(`${API}/jobs/${job.id}`, { headers });
        if (!response.ok) {
          throw new Error(`Impossibile leggere il job (${response.status})`);
        }
        const payload = await response.json();
        if (cancelled) return;
        setJob(payload);
        if (payload.status === "completed") {
          await refreshFiles(payload.output_slug || "");
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message || "Errore nel monitoraggio dello scraping");
        }
      }
    }, 2000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [job?.id, job?.status, refreshFiles]);

  async function startScrape(event) {
    event.preventDefault();
    setJobBusy(true);
    setError("");
    try {
      const headers = { "Content-Type": "application/json" };
      if (apiKey) headers["x-api-key"] = apiKey;
      const response = await fetch(`${API}/scrape`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          region: selectedRegion,
          include_comune_pec: includeComunePec,
          web_search: webSearch,
          pm_source: pmSource,
          strict,
          scrape_limit: scrapeLimit ? Number(scrapeLimit) : 0,
        }),
      });
      if (!response.ok) {
        throw new Error(`Impossibile avviare lo scraping (${response.status})`);
      }
      const payload = await response.json();
      setJob(payload);
    } catch (err) {
      setError(err.message || "Errore nell'avvio dello scraping");
    } finally {
      setJobBusy(false);
    }
  }

  async function cancelJob() {
    if (!job?.id) return;
    setError("");
    try {
      const headers = { "Content-Type": "application/json" };
      if (apiKey) headers["x-api-key"] = apiKey;
      const response = await fetch(`${API}/jobs/${job.id}/cancel`, {
        method: "POST",
        headers,
      });
      if (!response.ok) {
        throw new Error(`Impossibile cancellare il job (${response.status})`);
      }
      const payload = await response.json();
      setJob((prev) => ({ ...prev, status: payload.status || "cancelled" }));
    } catch (err) {
      setError(err.message || "Errore nella cancellazione del job");
    }
  }

  const rows = current?.rows || [];
  const sources = useMemo(() => {
    const values = new Set();
    rows.forEach((row) => {
      const value = (row.fonte || "").trim();
      if (value) values.add(value);
    });
    return Array.from(values).sort();
  }, [rows]);

  const filteredRows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return rows.filter((row) => {
      if (sourceFilter !== "all" && (row.fonte || "") !== sourceFilter) {
        return false;
      }
      if (!needle) {
        return true;
      }
      const haystack = [
        row.comune,
        row.provincia,
        row.sigla_provincia,
        row.pec,
        row.mail,
        row.matched_by,
        row.comuni_associati,
        row.unione,
        row.fonte,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(needle);
    });
  }, [rows, query, sourceFilter]);

  const summary = current?.summary || {};
  const selectedFile = current?.file || files.find((item) => item.slug === selectedSlug);
  const contactCount = summary.rows_with_contact ?? rows.filter((row) => row.pec || row.mail).length;
  const noContactCount = summary.rows_without_contact ?? rows.length - contactCount;
  const percentage = rows.length ? Math.round((contactCount / rows.length) * 100) : 0;
  const jobLabel = job?.status ? job.status.toUpperCase() : "IDLE";

  return (
    <div className="dashboard-shell">
      <div className="dashboard-noise" aria-hidden="true" />
      <main className="dashboard-container">
        <section className="hero-card">
          <div className="hero-copy">
            <span className="eyebrow">Polizia Locale Finder</span>
            <h1>Dashboard web per gli output del motore di ricerca</h1>
            <p>
              Visualizza i CSV/JSON generati dal backend, filtra per comune e fonte,
              e scarica i risultati senza rilanciare lo scraping.
            </p>
          </div>
          <div className="hero-meta">
            <label className="field">
              <span>Output</span>
              <select
                value={selectedSlug}
                onChange={(event) => setSelectedSlug(event.target.value)}
                disabled={loadingFiles || files.length === 0}
              >
                {files.length === 0 ? (
                  <option value="">Nessun output disponibile</option>
                ) : (
                  files.map((file) => (
                    <option key={file.slug} value={file.slug}>
                      {file.label}
                    </option>
                  ))
                )}
              </select>
            </label>
            <div className="download-links">
              {selectedSlug ? (
                <>
                  <a href={`${API}/outputs/${selectedSlug}/download/json`} target="_blank" rel="noreferrer">
                    JSON
                  </a>
                  <a href={`${API}/outputs/${selectedSlug}/download/csv`} target="_blank" rel="noreferrer">
                    CSV
                  </a>
                  <a href={`${API}/outputs/${selectedSlug}/download/xlsx`} target="_blank" rel="noreferrer">
                    XLSX
                  </a>
                </>
              ) : null}
            </div>
          </div>
        </section>

        {job ? (
          <section className="job-log-card">
            <div className="job-log-header">
              <h3>Job log — {job.id}</h3>
              <div>
                {job.status === "running" ? (
                  <button onClick={cancelJob}>Cancella job</button>
                ) : null}
              </div>
            </div>
            <pre className="job-log">
{(job.stdout || "").slice(-16000)}
            </pre>
          </section>
        ) : null}

        <section className="scrape-card">
          <div className="scrape-card-header">
            <div>
              <h2>Nuova ricerca</h2>
              <p>Avvia uno scraping su una regione diversa senza uscire dalla dashboard.</p>
            </div>
            <span className="job-pill">{jobLabel}</span>
          </div>

          <form className="scrape-grid" onSubmit={startScrape}>
            <label className="field">
              <span>Regione</span>
              <select value={selectedRegion} onChange={(event) => setSelectedRegion(event.target.value)}>
                {regions.length === 0 ? (
                  <option value="">Nessuna regione disponibile</option>
                ) : (
                  regions.map((region) => (
                    <option key={region.code} value={region.name}>
                      {region.name} [{region.code}]
                    </option>
                  ))
                )}
              </select>
            </label>

            <label className="field">
              <span>Limite scraping</span>
              <input
                type="number"
                min="0"
                placeholder="0 = senza limite"
                value={scrapeLimit}
                onChange={(event) => setScrapeLimit(event.target.value)}
              />
            </label>

            <div className="scrape-options">
              <label><input type="checkbox" checked={includeComunePec} onChange={(event) => setIncludeComunePec(event.target.checked)} /> PEC generica del Comune</label>
              <label><input type="checkbox" checked={webSearch} onChange={(event) => setWebSearch(event.target.checked)} /> Web search</label>
              <label><input type="checkbox" checked={pmSource} onChange={(event) => setPmSource(event.target.checked)} /> Fonte PM</label>
              <label><input type="checkbox" checked={strict} onChange={(event) => setStrict(event.target.checked)} /> Modalità strict</label>
            </div>

            <div className="scrape-actions">
              <button type="submit" disabled={jobBusy || job?.status === "running" || !selectedRegion}>
                {jobBusy ? "Avvio..." : "Avvia scraping"}
              </button>
              {job ? <span className="job-status">{job.status}{job.exit_code != null ? ` · exit ${job.exit_code}` : ""}</span> : null}
              {job ? (
                <div className="job-progress">
                  {/* determinate when progress > 0, otherwise show an indeterminate animated bar */}
                  {job.progress && job.progress > 0 ? (
                    <>
                      <div className="job-progress-bar" style={{ width: `${job.progress}%` }} />
                      <small>{job.progress ? `${job.progress}%` : ""}</small>
                    </>
                  ) : (
                    <>
                      <div className="job-progress-bar indeterminate" />
                      <small>Avviato…</small>
                    </>
                  )}
                </div>
              ) : null}
            </div>
            <label className="field field-grow">
              <span>API Key (opzionale)</span>
              <input
                type="password"
                placeholder="Inserisci API key per avviare/cancellare job"
                value={apiKey}
                onChange={(e) => {
                  setApiKey(e.target.value);
                  try { localStorage.setItem("POLIZIA_DASH_API_KEY", e.target.value); } catch {}
                }}
              />
            </label>
          </form>
        </section>

        {error ? <div className="alert-card">{error}</div> : null}

        <section className="stats-grid">
          <article className="stat-card">
            <span className="stat-label">Comuni</span>
            <strong>{rows.length || "-"}</strong>
            <small>{selectedFile ? selectedFile.label : "Seleziona un output"}</small>
          </article>
          <article className="stat-card">
            <span className="stat-label">Copertura</span>
            <strong>{percentage}%</strong>
            <small>{contactCount} con contatto pubblico</small>
          </article>
          <article className="stat-card">
            <span className="stat-label">Senza contatto</span>
            <strong>{noContactCount}</strong>
            <small>{summary.not_found_rows ?? noContactCount} segnati non trovati</small>
          </article>
          <article className="stat-card">
            <span className="stat-label">Confidence media</span>
            <strong>{summary.avg_confidence ?? 0}</strong>
            <small>Ultimo aggiornamento {formatDate(selectedFile?.updated_at)}</small>
          </article>
        </section>

        <section className="controls-card">
          <label className="field field-grow">
            <span>Cerca</span>
            <input
              type="search"
              placeholder="Comune, provincia, email, fonte..."
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <label className="field">
            <span>Fonte</span>
            <select value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}>
              <option value="all">Tutte</option>
              {sources.map((source) => (
                <option key={source} value={source}>
                  {source}
                </option>
              ))}
            </select>
          </label>
        </section>

        <section className="table-card">
          <div className="table-card-header">
            <div>
              <h2>Risultati</h2>
              <p>
                {loadingRows ? "Caricamento in corso..." : `${filteredRows.length} record visualizzati`}
              </p>
            </div>
            <span className="table-hint">{loadingFiles ? "Carico elenco output..." : "Filtro locale sul browser"}</span>
          </div>

          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Comune</th>
                  <th>Contatto</th>
                  <th>Fonte</th>
                  <th>Confidence</th>
                </tr>
              </thead>
              <tbody>
                {filteredRows.length === 0 ? (
                  <tr>
                    <td colSpan="4" className="empty-row">
                      Nessun record da mostrare.
                    </td>
                  </tr>
                ) : (
                  filteredRows.map((row) => {
                    const hasPec = Boolean(row.pec);
                    const hasMail = Boolean(row.mail);
                    const contact = hasPec && hasMail ? `${row.pec} · ${row.mail}` : row.pec || row.mail || "NON TROVATO";
                    const badgeClass = hasPec
                      ? "badge badge-pec"
                      : hasMail
                        ? "badge badge-mail"
                        : "badge badge-empty";
                    return (
                      <tr key={`${row.codice_istat || row.comune}-${row.fonte || contact}`}>
                        <td>
                          <div className="cell-main">
                            <strong>{row.comune || "-"}</strong>
                            <span>
                              {row.provincia || "-"}
                              {row.sigla_provincia ? ` · ${row.sigla_provincia}` : ""}
                            </span>
                          </div>
                        </td>
                        <td>
                          <div className="contact-stack">
                            <span className="contact-badges">
                              {hasPec ? <span className="badge badge-pec">PEC</span> : null}
                              {hasMail ? <span className="badge badge-mail">MAIL</span> : null}
                              {!hasPec && !hasMail ? <span className="badge badge-empty">VUOTO</span> : null}
                            </span>
                            <span className="contact-value">{contact}</span>
                          </div>
                        </td>
                        <td>
                          <span className="source-pill">{row.fonte || "-"}</span>
                        </td>
                        <td>
                          <span className="confidence-pill">{row.confidence ?? 0}</span>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </section>
      </main>
    </div>
  );
}

export default App;