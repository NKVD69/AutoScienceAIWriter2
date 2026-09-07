/**
 * SPIKE 03 — Pyodide : isolation et couverture scientifique du niveau 1.
 *
 * Hypothèses testées
 *   H3.1  Pyodide charge numpy, pandas, scipy, matplotlib, sympy, scikit-learn.
 *   H3.2  L'accès réseau est bloqué depuis le code invité.
 *   H3.3  L'accès au système de fichiers hôte est bloqué ; seuls les montages
 *         explicites sont lisibles.
 *   H3.4  Un script matplotlib produit un PNG exploitable dans le FS virtuel.
 *   H3.5  Un timeout externe interrompt une boucle infinie.
 *   H3.6  Le temps de démarrage à froid reste acceptable.
 *
 * Décision associée : ADR-005. Un échec de H3.2 ou H3.3 invalide le niveau 1
 * et impose de reconcevoir l'isolation du code produit par les agents.
 *
 * Prérequis : node >= 20, npm i pyodide
 * Usage : node spike_03_pyodide.mjs
 * Sortie : 0 conforme · 1 non conforme · 2 environnement insuffisant
 */
import { writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`  [${ok ? "OK " : "ÉCHEC"}] ${name}${detail ? " — " + detail : ""}`);
  return ok;
};

const PACKAGES = ["numpy", "pandas", "scipy", "matplotlib", "sympy", "scikit-learn"];

let loadPyodide;
try {
  ({ loadPyodide } = await import("pyodide"));
} catch {
  console.error("Paquet pyodide absent. npm i pyodide");
  process.exit(2);
}

console.log("SPIKE 03 — Pyodide\n");
console.log(`  node ${process.version} · ${process.platform}\n`);

// ---- H3.6 démarrage à froid -------------------------------------------
// Distribution Pyodide : par défaut, loadPackage télécharge les wheels depuis
// un CDN. Pour un produit local-first (ADR-010), la distribution complète doit
// être vendorisée hors ligne et indexURL doit pointer dessus.
const INDEX_URL = process.env.PYODIDE_INDEX_URL || undefined;
if (!INDEX_URL) {
  console.log("  AVERTISSEMENT : PYODIDE_INDEX_URL non défini.");
  console.log("  loadPackage ira chercher les wheels sur cdn.jsdelivr.net,");
  console.log("  ce qui est incompatible avec le mode local strict.\n");
}
const t0 = performance.now();
const py = await loadPyodide(INDEX_URL ? { indexURL: INDEX_URL } : undefined);
const bootMs = performance.now() - t0;
console.log("H3.6 — Démarrage");
check("démarrage < 10 s", bootMs < 10000, `${(bootMs / 1000).toFixed(1)} s`);

// ---- H3.1 paquets ------------------------------------------------------
console.log("\nH3.1 — Paquets scientifiques");
const tPkg = performance.now();
try { await py.loadPackage(PACKAGES); } catch (e) {
  console.log(`     loadPackage a signalé : ${String(e).split("\n")[0]}`);
}
console.log(`     loadPackage : ${((performance.now() - tPkg) / 1000).toFixed(1)} s`);

// Le verdict porte sur l'IMPORT, pas sur l'absence d'exception de loadPackage :
// loadPackage journalise ses échecs sans lever, et conclure de son silence que
// les paquets sont présents produirait un faux positif.
for (const mod of ["numpy", "pandas", "scipy.stats", "sympy", "sklearn", "matplotlib"]) {
  try {
    await py.runPythonAsync(`import ${mod}`);
    check(`import ${mod}`, true);
  } catch (e) {
    check(`import ${mod}`, false, String(e).split("\n").pop());
  }
}

// ---- H3.2 réseau -------------------------------------------------------
console.log("\nH3.2 — Isolation réseau");
const netProbe = `
import json
out = {}
try:
    import urllib.request
    urllib.request.urlopen("http://example.com", timeout=3)
    out["urllib"] = "AUTORISÉ"
except Exception as e:
    out["urllib"] = f"bloqué ({type(e).__name__})"
# Un connect() qui ne lève pas ne prouve pas qu'une connexion existe :
# l'implémentation emscripten peut être un talon. On exige un aller-retour.
try:
    import socket
    s = socket.socket(); s.settimeout(3)
    s.connect(("example.com", 80))
    s.sendall(b"GET / HTTP/1.0\r\nHost: example.com\r\n\r\n")
    data = s.recv(16)
    out["socket"] = "EXFILTRATION RÉELLE" if data else "connect() sans échange (talon)"
except Exception as e:
    out["socket"] = f"bloqué ({type(e).__name__})"
json.dumps(out)
`;
const net = JSON.parse(await py.runPythonAsync(netProbe));
check("urllib bloqué", !net.urllib.includes("AUTORISÉ"), net.urllib);
check("aucun aller-retour socket", !net.socket.startsWith("EXFILTRATION"), net.socket);

// ---- H3.3 système de fichiers -----------------------------------------
console.log("\nH3.3 — Isolation du système de fichiers");
const hostDir = mkdtempSync(join(tmpdir(), "spike03_"));
const secret = join(hostDir, "secret.txt");
writeFileSync(secret, "CONTENU HÔTE NE DEVANT PAS ÊTRE LU");

const fsProbe = `
import json, os
out = {}
for path in ["/etc/passwd", ${JSON.stringify(secret)}]:
    try:
        with open(path) as f:
            f.read(32)
        out[path] = "LU"
    except Exception as e:
        out[path] = f"bloqué ({type(e).__name__})"
out["cwd_listing"] = sorted(os.listdir("/"))[:8]
json.dumps(out)
`;
const fsr = JSON.parse(await py.runPythonAsync(fsProbe));
check("/etc/passwd hôte non lisible", fsr["/etc/passwd"] !== "LU", fsr["/etc/passwd"]);
check("fichier hôte arbitraire non lisible", fsr[secret] !== "LU", fsr[secret]);
console.log(`     racine virtuelle : ${fsr.cwd_listing.join(" ")}`);

// montage explicite en lecture seule
console.log("\n     Montage explicite d'un répertoire de données");
try {
  py.FS.mkdirTree("/data");
  py.FS.mount(py.FS.filesystems.NODEFS, { root: hostDir }, "/data");
  const mounted = await py.runPythonAsync(`open("/data/secret.txt").read()[:8]`);
  check("montage explicite lisible", mounted.startsWith("CONTENU"), `lu : ${mounted}…`);
} catch (e) {
  check("montage explicite lisible", false, String(e).split("\n")[0]);
}

// ---- H3.4 figure matplotlib -------------------------------------------
console.log("\nH3.4 — Production d'une figure");
try {
  const size = await py.runPythonAsync(`
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt, numpy as np, os
rng = np.random.default_rng(42)
x = np.linspace(0, 10, 200)
plt.figure(figsize=(6,4)); plt.plot(x, np.sin(x) + rng.normal(0, .05, 200))
plt.xlabel("x"); plt.ylabel("y"); plt.title("spike 03")
os.makedirs("/out", exist_ok=True)
plt.savefig("/out/fig.png", dpi=150); plt.close()
os.path.getsize("/out/fig.png")
`);
  check("PNG produit dans le FS virtuel", size > 5000, `${(size / 1024).toFixed(0)} Ko`);
} catch (e) {
  check("PNG produit dans le FS virtuel", false, String(e).split("\n").pop());
}

// ---- H3.5 timeout ------------------------------------------------------
console.log("\nH3.5 — Interruption d'une boucle infinie");
console.log("     Note : Pyodide s'exécute dans le thread appelant ; l'interruption");
console.log("     exige un SharedArrayBuffer d'interruption ou un worker dédié.");
try {
  const buf = new Uint8Array(new SharedArrayBuffer(1));
  py.setInterruptBuffer(buf);
  const timer = setTimeout(() => { buf[0] = 2; }, 2000); // SIGINT
  const started = performance.now();
  try {
    await py.runPythonAsync(`
while True:
    pass
`);
    clearTimeout(timer);
    check("boucle infinie interrompue", false, "le script s'est terminé seul");
  } catch (e) {
    clearTimeout(timer);
    const ms = performance.now() - started;
    check("boucle infinie interrompue", ms < 6000, `${(ms / 1000).toFixed(1)} s · ${String(e).split("\n").pop()}`);
  }
} catch (e) {
  check("interruption disponible", false, `SharedArrayBuffer indisponible : ${e}`);
}

// ---- verdict -----------------------------------------------------------
const failed = results.filter((r) => !r.ok);
console.log("\n" + "=".repeat(62));
console.log(`SPIKE 03 : ${results.length - failed.length}/${results.length} conformes`);
if (failed.length) console.log("Non conformes : " + failed.map((f) => f.name).join(", "));
process.exit(failed.length ? 1 : 0);
