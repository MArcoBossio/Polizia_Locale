import { useEffect, useMemo, useState } from "react";
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
  const [query, setQuery] = useState("");
  const [sourceFilter, setSourceFilter] = useState("all");

  useEffect(() => {
    let cancelled = false;
    async function loadFiles() {
      setLoadingFiles(true);
      setError("");
      try {
        const response = await fetch(`${API}/outputs`);
        if (!response.ok) {
          throw new Error(`Impossibile leggere gli output (${response.status})`);
        }
        const payload = await response.json();
        if (cancelled) return;
        const nextFiles = payload.files || [];
        setFiles(nextFiles);
        if (nextFiles.length > 0) {
          setSelectedSlug((prev) => prev || nextFiles[0].slug);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message || "Errore nel caricamento degli output");
        }
      } finally {
        if (!cancelled) {
          setLoadingFiles(false);
        }
      }
    }
    loadFiles();
    return () => {
      cancelled = true;
    };
  }, []);

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
                    const contact = row.pec || row.mail || "NON TROVATO";
                    const badgeClass = row.pec
                      ? "badge badge-pec"
                      : row.mail
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
                            <span className={badgeClass}>{row.pec ? "PEC" : row.mail ? "MAIL" : "VUOTO"}</span>
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