"use strict";

const $ = (s, el = document) => el.querySelector(s);
const formulaire = $("#filtres");
const NOUVEAU_MS = 48 * 3600 * 1000;

// Dépôt GitHub déduit de l'adresse GitHub Pages (pseudo.github.io/depot/).
const depot = location.hostname.endsWith("github.io")
  ? { proprio: location.hostname.split(".")[0], nom: location.pathname.split("/")[1] }
  : null;

const LIENS_EXTERNES = [
  ["Leboncoin", "https://www.leboncoin.fr/recherche?category=10&locations=Moliets-et-Maa_40660,Soustons_40140,Vieux-Boucau-les-Bains_40480,Linxe_40260,L%C3%A9on_40550&real_estate_type=1,2"],
  ["SeLoger", "https://www.seloger.com/"],
  ["PAP", "https://www.pap.fr/annonce/locations-soustons-40140-g22749g22971g22948g22795g22959"],
];

let donnees = { annonces: [], sources: {} };
let criteres = {};

const euros = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const nombre = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 });

function ilYA(iso) {
  if (!iso) return "";
  const min = Math.round((Date.now() - new Date(iso)) / 60000);
  if (min < 1) return "à l'instant";
  if (min < 60) return `il y a ${min} min`;
  const h = Math.round(min / 60);
  if (h < 24) return `il y a ${h} h`;
  const j = Math.round(h / 24);
  return j === 1 ? "hier" : `il y a ${j} jours`;
}

function cle(t) {
  return (t || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z]/g, "");
}

// --- Filtres <-> URL (#...) --------------------------------------------------

function filtresDepuisCriteres(c) {
  return {
    villes: c.villes || [],
    type: c.types && c.types.length === 1 ? c.types[0] : "",
    loyer_max: c.loyer_max ?? "",
    pieces_min: c.pieces_min ?? "",
    chambres_min: c.chambres_min ?? "",
    surface_min: c.surface_min ?? "",
    meuble: c.meuble === true ? "oui" : c.meuble === false ? "non" : "",
    sans_saison: !!c.exclure_saisonnier,
    retirees: false,
  };
}

function lireFormulaire() {
  const f = new FormData(formulaire);
  return {
    villes: f.getAll("ville"),
    type: f.get("type") || "",
    loyer_max: f.get("loyer_max") || "",
    pieces_min: f.get("pieces_min") || "",
    chambres_min: f.get("chambres_min") || "",
    surface_min: f.get("surface_min") || "",
    meuble: f.get("meuble") || "",
    sans_saison: f.has("sans_saison"),
    retirees: f.has("retirees"),
  };
}

function remplirFormulaire(v) {
  for (const cb of formulaire.querySelectorAll('input[name="ville"]')) cb.checked = v.villes.some((x) => cle(x) === cle(cb.value));
  for (const nom of ["type", "pieces_min", "chambres_min", "meuble"]) {
    const r = formulaire.querySelector(`input[name="${nom}"][value="${v[nom]}"]`);
    if (r) r.checked = true;
  }
  formulaire.loyer_max.value = v.loyer_max;
  formulaire.surface_min.value = v.surface_min;
  formulaire.sans_saison.checked = v.sans_saison;
  formulaire.retirees.checked = v.retirees;
}

function versHash(v) {
  const p = new URLSearchParams();
  for (const [k, val] of Object.entries(v)) {
    if (k === "villes") p.set(k, val.join("|"));
    else if (val !== "" && val !== false) p.set(k, val === true ? "1" : val);
  }
  p.set("tri", $("#tri").value);
  history.replaceState(null, "", "#" + p.toString());
}

function depuisHash() {
  if (location.hash.length < 2) return null;
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get("tri")) $("#tri").value = p.get("tri");
  return {
    villes: (p.get("villes") || "").split("|").filter(Boolean),
    type: p.get("type") || "",
    loyer_max: p.get("loyer_max") || "",
    pieces_min: p.get("pieces_min") || "",
    chambres_min: p.get("chambres_min") || "",
    surface_min: p.get("surface_min") || "",
    meuble: p.get("meuble") || "",
    sans_saison: p.has("sans_saison"),
    retirees: p.has("retirees"),
  };
}

// --- Rendu ------------------------------------------------------------------

function garde(a, v) {
  if (!a.actif && !v.retirees) return false;
  if (v.villes.length && !v.villes.some((x) => cle(x) === cle(a.ville))) return false;
  if (v.type && a.type !== v.type) return false;
  // Valeur inconnue = on garde l'annonce (mieux vaut la voir que la rater).
  const min = (val, seuil) => seuil === "" || val == null || val >= Number(seuil);
  if (v.loyer_max !== "" && a.loyer != null && a.loyer > Number(v.loyer_max)) return false;
  if (!min(a.pieces, v.pieces_min) || !min(a.chambres, v.chambres_min) || !min(a.surface, v.surface_min)) return false;
  if (v.meuble && a.meuble != null && a.meuble !== (v.meuble === "oui")) return false;
  if (v.sans_saison && a.saisonnier) return false;
  return true;
}

const TRIS = {
  "recent": (a, b) => (b.publie || b.vu_le).localeCompare(a.publie || a.vu_le),
  "loyer-asc": (a, b) => (a.loyer ?? 1e9) - (b.loyer ?? 1e9),
  "loyer-desc": (a, b) => (b.loyer ?? 0) - (a.loyer ?? 0),
  "surface-desc": (a, b) => (b.surface ?? 0) - (a.surface ?? 0),
};

function carte(a) {
  const el = $("#modele-carte").content.firstElementChild.cloneNode(true);
  const img = $("img", el);
  if (a.photo) {
    img.src = a.photo;
    img.onerror = () => el.querySelector(".carte__media").classList.add("carte__media--vide");
  } else {
    $(".carte__media", el).classList.add("carte__media--vide");
  }
  $(".prix", el).textContent = a.loyer != null ? euros.format(a.loyer) : "Loyer ?";
  if (a.ancien_loyer && a.ancien_loyer > a.loyer) {
    const s = $(".prix__ancien", el);
    s.hidden = false;
    s.textContent = euros.format(a.ancien_loyer);
  }

  const badges = [];
  const recent = Date.now() - new Date(a.vu_le) < NOUVEAU_MS && (!a.publie || Date.now() - new Date(a.publie) < NOUVEAU_MS);
  if (!a.actif) badges.push(["Retirée", "retiree"]);
  else if (recent) badges.push(["Nouveau", "nouveau"]);
  if (a.ancien_loyer) badges.push(["Baisse de prix", ""]);
  if (a.saisonnier) badges.push(["Peut-être saisonnière", "saison"]);
  for (const [texte, mod] of badges) {
    const li = document.createElement("li");
    li.className = "badge" + (mod ? ` badge--${mod}` : "");
    li.textContent = texte;
    $(".badges", el).append(li);
  }

  const lien = $(".carte__lien", el);
  lien.href = a.url;
  lien.textContent = `${a.type === "maison" ? "Maison" : "Appartement"} à ${a.ville}`;

  $(".carte__infos", el).textContent = [
    a.pieces && `${a.pieces} pièce${a.pieces > 1 ? "s" : ""}`,
    a.chambres && `${a.chambres} ch.`,
    a.surface && `${nombre.format(a.surface)} m²`,
    a.meuble === true ? "meublé" : a.meuble === false ? "vide" : null,
  ].filter(Boolean).join(" · ");
  $(".carte__extrait", el).textContent = a.extrait || "";
  $(".carte__pied", el).textContent = [
    a.source,
    a.publie ? `publiée ${ilYA(a.publie)}` : `repérée ${ilYA(a.vu_le)}`,
    !a.actif && a.retire_le ? `retirée ${ilYA(a.retire_le)}` : null,
  ].filter(Boolean).join(" · ");
  if (!a.actif) el.classList.add("carte--retiree");
  return el;
}

function afficher() {
  const v = lireFormulaire();
  versHash(v);
  const visibles = donnees.annonces.filter((a) => garde(a, v)).sort(TRIS[$("#tri").value]);
  $("#liste").replaceChildren(...visibles.map(carte));
  $("#vide").hidden = visibles.length > 0;
  const actives = visibles.filter((a) => a.actif).length;
  $("#total").textContent = `${actives} annonce${actives > 1 ? "s" : ""}` + (visibles.length > actives ? ` (+ ${visibles.length - actives} retirée${visibles.length - actives > 1 ? "s" : ""})` : "");

  const nbFiltres = [v.type, v.loyer_max, v.pieces_min, v.chambres_min, v.surface_min, v.meuble].filter(Boolean).length + (v.sans_saison ? 1 : 0);
  const pastille = $("#nb-filtres");
  pastille.hidden = nbFiltres === 0;
  pastille.textContent = nbFiltres;
  majBoutonAlertes(v);
}

function construireCommunes() {
  const miennes = criteres.villes || [];
  const autres = [...new Set(donnees.communes || donnees.annonces.map((a) => a.ville))]
    .filter((x) => !miennes.some((m) => cle(m) === cle(x)))
    .sort((a, b) => a.localeCompare(b, "fr"));
  const compte = (ville) => donnees.annonces.filter((a) => a.actif && cle(a.ville) === cle(ville)).length;
  const ligne = (ville) => {
    const l = document.createElement("label");
    l.className = "commune";
    l.innerHTML = `<input type="checkbox" name="ville"><span></span><span class="commune__nb"></span>`;
    l.querySelector("input").value = ville;
    l.querySelector("span").textContent = ville;
    l.querySelector(".commune__nb").textContent = compte(ville);
    return l;
  };
  const conteneur = $("#communes");
  conteneur.replaceChildren(...miennes.map(ligne));
  if (autres.length) {
    const sep = document.createElement("p");
    sep.className = "communes__sep";
    sep.textContent = "Voisines";
    conteneur.append(sep, ...autres.map(ligne));
  }
}

function resumeCriteres(c) {
  const morceaux = [
    (c.villes || []).join(", "),
    c.types && c.types.length === 1 ? (c.types[0] === "maison" ? "maisons" : "appartements") : null,
    c.loyer_max ? `≤ ${euros.format(c.loyer_max)}` : null,
    c.pieces_min ? `${c.pieces_min} pièces et +` : null,
    c.chambres_min ? `${c.chambres_min} ch. et +` : null,
    c.surface_min ? `${c.surface_min} m² et +` : null,
    c.meuble === true ? "meublé" : c.meuble === false ? "vide" : null,
    c.exclure_saisonnier ? "hors saisonnier" : null,
  ].filter(Boolean);
  $("#resume-criteres").textContent = "Une notification pour chaque nouvelle annonce : " + morceaux.join(" · ") + ".";
}

// Filtres affichés -> critères d'alerte (même forme que docs/criteres.json).
function filtresVersCriteres(v) {
  const toutes = [...formulaire.querySelectorAll('input[name="ville"]')].map((cb) => cb.value);
  const n = (x) => (x === "" ? null : Number(x));
  return {
    villes: v.villes.length ? v.villes : toutes,
    types: v.type ? [v.type] : ["maison", "appartement"],
    loyer_max: n(v.loyer_max),
    pieces_min: n(v.pieces_min),
    chambres_min: n(v.chambres_min),
    surface_min: n(v.surface_min),
    meuble: v.meuble === "oui" ? true : v.meuble === "non" ? false : null,
    exclure_saisonnier: v.sans_saison,
  };
}

function memesCriteres(a, b) {
  const norme = (c) => JSON.stringify({ ...c, _aide: undefined, villes: [...c.villes].map(cle).sort(), types: [...c.types].sort() });
  return norme(a) === norme(b);
}

function envoyerVersAlertes() {
  const c = filtresVersCriteres(lireFormulaire());
  const corps = "Envoyé depuis l'interface. Clique sur **Create** (ou **Submit new issue**) pour appliquer ces critères à tes alertes Telegram.\n\n```json\n" + JSON.stringify(c, null, 2) + "\n```\n";
  const url = `https://github.com/${depot.proprio}/${depot.nom}/issues/new?title=${encodeURIComponent("Critères d'alerte")}&body=${encodeURIComponent(corps)}`;
  window.open(url, "_blank", "noopener");
  $("#aide-alertes").hidden = false;
}

function majBoutonAlertes(v) {
  const bouton = $("#vers-alertes"), ecart = $("#ecart-alertes");
  if (!depot) {
    bouton.disabled = true;
    ecart.hidden = false;
    ecart.textContent = "Disponible seulement sur la page en ligne (GitHub Pages).";
    return;
  }
  const identiques = criteres.villes && memesCriteres(filtresVersCriteres(v), criteres);
  bouton.disabled = identiques;
  ecart.hidden = false;
  ecart.textContent = identiques ? "Les filtres actuels sont déjà tes critères d'alerte." : "Les filtres actuels diffèrent de tes critères d'alerte.";
}

async function etat() {
  let derniere = donnees.maj, ok = true;
  if (depot) {
    try {
      const r = await fetch(`https://api.github.com/repos/${depot.proprio}/${depot.nom}/actions/workflows/veille.yml/runs?per_page=1&status=completed`);
      const run = (await r.json()).workflow_runs?.[0];
      if (run) { derniere = run.updated_at; ok = run.conclusion === "success"; }
    } catch { /* limite d'API atteinte : on garde la date des données */ }
  }
  const enPanne = Object.entries(donnees.sources).filter(([, s]) => s.echecs > 0).map(([n]) => n);
  ok = ok && enPanne.length === 0;
  const el = $("#etat");
  el.innerHTML = `<span class="pastille${ok ? "" : " pastille--ko"}"></span>`;
  el.append(ok ? "Vérifié " : "Problème · vérifié ");
  const b = document.createElement("strong");
  b.textContent = ilYA(derniere);
  el.append(b, enPanne.length ? ` · source en panne : ${enPanne.join(", ")}` : " · toutes les 10 min");
}

async function charger() {
  const [d, c] = await Promise.all([
    fetch("data/annonces.json?" + Date.now()).then((r) => r.json()),
    fetch("criteres.json?" + Date.now()).then((r) => r.json()),
  ]);
  const premierChargement = !donnees.maj;
  const v = premierChargement ? null : lireFormulaire();
  donnees = d;
  criteres = c;
  construireCommunes();
  remplirFormulaire(v || depuisHash() || filtresDepuisCriteres(criteres));
  resumeCriteres(criteres);
  afficher();
  etat();
}

// --- Événements -------------------------------------------------------------

formulaire.addEventListener("input", afficher);
formulaire.addEventListener("reset", () => setTimeout(() => {
  for (const cb of formulaire.querySelectorAll('input[name="ville"]')) cb.checked = false;
  afficher();
}));
formulaire.addEventListener("submit", (e) => e.preventDefault());
$("#tri").addEventListener("change", afficher);
$("#reprendre").addEventListener("click", () => { remplirFormulaire(filtresDepuisCriteres(criteres)); afficher(); });
$("#vers-alertes").addEventListener("click", envoyerVersAlertes);
document.querySelectorAll("[data-communes]").forEach((b) => b.addEventListener("click", () => {
  const miennes = (criteres.villes || []).map(cle);
  for (const cb of formulaire.querySelectorAll('input[name="ville"]')) {
    cb.checked = b.dataset.communes === "toutes" || miennes.includes(cle(cb.value));
  }
  afficher();
}));

// Filtres repliés par défaut sur téléphone.
if (matchMedia("(max-width: 899px)").matches) $("#filtres-details").open = false;

$("#liens-externes").replaceChildren(...LIENS_EXTERNES.map(([nom, url]) => {
  const li = document.createElement("li");
  li.innerHTML = `<a class="lien-fleche" target="_blank" rel="noopener"></a>`;
  li.firstChild.href = url;
  li.firstChild.textContent = nom;
  return li;
}));

charger().catch(() => { $("#etat").textContent = "Impossible de charger les annonces."; });
setInterval(() => charger().catch(() => {}), 5 * 60 * 1000);
