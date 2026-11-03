"""Veille des locations à l'année (côte sud des Landes).

Récupère les annonces Bien'ici + PAP, met à jour docs/data/annonces.json
et envoie une alerte Telegram pour chaque nouvelle annonce qui colle aux
critères de docs/criteres.json.

Secrets (variables d'environnement) : TELEGRAM_TOKEN, TELEGRAM_CHAT_ID.
Sans eux, les alertes sont seulement affichées dans la console.
"""
import html
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

RACINE = Path(__file__).resolve().parent.parent
FICHIER_DONNEES = RACINE / "docs" / "data" / "annonces.json"
FICHIER_CRITERES = RACINE / "docs" / "criteres.json"

UA = "Mozilla/5.0 (compatible; veille-location-landes; usage personnel)"
GARDER_RETIREES = timedelta(days=14)
ALERTE_APRES_ECHECS = 3

# Communes surveillées : nom affiché, code postal, id de zone Bien'ici, id PAP.
COMMUNES = [
    ("Moliets-et-Maa", "40660", "-75855", "22971"),
    ("Soustons", "40140", "-75862", "22749"),
    ("Vieux-Boucau-les-Bains", "40480", "-75853", "22948"),
    ("Linxe", "40260", "-75871", "22795"),
    ("Léon", "40550", "-75858", "22959"),
    # Voisines : visibles dans l'interface, alertes seulement si ajoutées aux critères.
    ("Messanges", "40660", "-75854", "22970"),
    ("Azur", "40140", "-75859", "22701"),
    ("Seignosse", "40510", "-75972", "22964"),
    ("Vielle-Saint-Girons", "40560", "-111430", "22967"),
    ("Saint-Michel-Escalus", "40550", "-75868", "22965"),
    ("Magescq", "40140", "-75864", "22724"),
]

# Indices d'une location qui n'est pas vraiment « à l'année ».
MOTIF_SAISONNIER = re.compile(
    r"saisonni|bail mobilit|location (de )?vacances|hors saison|ann[ée]e scolaire"
    r"|jusqu'(au|à|en)\s+(fin\s+)?(mai|juin|juillet)"
    r"|(de|d')\s*(septembre|octobre|novembre)\s+(à|a|au|jusqu)\S*\s+(fin\s+)?(mai|juin|juillet)",
    re.I,
)


def cle(texte):
    """Forme normalisée d'un nom de commune pour comparer (sans accents, tirets, casse)."""
    t = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", t.lower())


NOM_PAR_CLE = {cle(nom): nom for nom, *_ in COMMUNES}


def maintenant():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def nombre(texte):
    m = re.search(r"\d+(?:[.,]\d+)?", (texte or "").replace(" ", "").replace(".", ""))
    return float(m.group().replace(",", ".")) if m else None


def est_saisonnier(*textes):
    return bool(MOTIF_SAISONNIER.search(" ".join(t or "" for t in textes)))


# --- Sources ---------------------------------------------------------------

def source_bienici(client):
    filtres = {
        "size": 100, "from": 0, "filterType": "rent",
        "propertyType": ["house", "flat"],
        "sortBy": "publicationDate", "sortOrder": "desc", "onTheMarket": [True],
        "zoneIdsByTypes": {"zoneIds": [c[2] for c in COMMUNES]},
    }
    r = client.get("https://www.bienici.com/realEstateAds.json",
                   params={"filters": json.dumps(filtres)})
    r.raise_for_status()
    annonces = []
    for a in r.json()["realEstateAds"]:
        publie = a.get("publicationDate")
        if not publie or publie.startswith("1970"):  # date absente chez certaines agences
            publie = a.get("modificationDate")
        photos = a.get("photos") or []
        description = html.unescape(re.sub(r"<[^>]+>", " ", a.get("description") or ""))
        annonces.append({
            "id": "bienici:" + a["id"],
            "source": "Bien'ici",
            "url": "https://www.bienici.com/annonce/" + a["id"],
            "titre": (a.get("title") or "").strip(),
            "ville": NOM_PAR_CLE.get(cle(a.get("city")), a.get("city")),
            "cp": a.get("postalCode"),
            "type": "maison" if a.get("propertyType") == "house" else "appartement",
            "loyer": a.get("price"),
            "pieces": a.get("roomsQuantity"),
            "chambres": a.get("bedroomsQuantity"),
            "surface": a.get("surfaceArea"),
            "meuble": a.get("isFurnished"),
            "photo": photos[0].get("url") if photos else None,
            "publie": publie,
            "saisonnier": est_saisonnier(a.get("title"), description),
            "extrait": re.sub(r"\s+", " ", description)[:220],
        })
    return annonces


def source_pap(client):
    ids = "".join("g" + c[3] for c in COMMUNES)
    r = client.get(f"https://www.pap.fr/annonce/locations-soustons-40140-{ids}", follow_redirects=True)
    r.raise_for_status()
    page = r.text
    # Chaque annonce commence par un bloc « item-thumb » contenant ses photos.
    blocs = re.split(r'<div class="search-list-item', page)[1:]
    annonces = []
    for bloc in blocs:
        lien = re.search(r'class="item-title" href="(/annonces/([a-z-]+?)-[a-z0-9-]*-r(\d+))"', bloc)
        if not lien:
            continue
        chemin, genre, num = lien.groups()
        ville_cp = re.search(r'<span class="h1">\s*([^<(]+?)\s*\((\d{5})\)', bloc)
        ville = ville_cp.group(1).strip() if ville_cp else ""
        nom = NOM_PAR_CLE.get(cle(ville))
        if not nom:  # PAP élargit aux communes alentour : on ne garde que les nôtres
            continue
        tags = [html.unescape(t) for t in re.findall(r"<li>(.*?)</li>", bloc.split('class="item-tags"', 1)[-1].split("</ul>")[0])]
        tag = lambda mot: next((nombre(t) for t in tags if mot in t), None)
        prix = re.search(r'class="item-price">\s*([^<]+)', bloc)
        photo = re.search(r'<img src="(https://cdn\.pap\.fr/photos/[^"]+)"', bloc)
        desc = re.search(r'class="item-description"[^>]*>(.*?)</p>', bloc, re.S)
        description = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", desc.group(1)))).strip() if desc else ""
        pieces = tag("pièce")
        annonces.append({
            "id": "pap:" + num,
            "source": "PAP",
            "url": "https://www.pap.fr" + chemin,
            "titre": f"{genre.capitalize()} {int(pieces)} pièces" if pieces else genre.capitalize(),
            "ville": nom,
            "cp": ville_cp.group(2),
            "type": "maison" if genre == "maison" else "appartement",
            "loyer": nombre(html.unescape(prix.group(1))) if prix else None,
            "pieces": int(pieces) if pieces else (1 if genre == "studio" else None),
            "chambres": int(c) if (c := tag("chambre")) else None,
            "surface": tag("m²"),
            "meuble": True if re.search(r"\bmeubl", description, re.I) else None,
            "photo": photo.group(1) if photo else None,
            "publie": None,
            "saisonnier": est_saisonnier(description),
            "extrait": description[:220],
        })
    return annonces


# PAP (derrière Cloudflare) refuse les programmes depuis novembre 2026 : en pause.
# On ne contourne pas ce blocage ; remettre "pap": source_pap s'il se lève.
SOURCES = {"bienici": source_bienici}


# --- Critères & alertes ----------------------------------------------------

def correspond(a, crit):
    if crit.get("villes") and cle(a["ville"]) not in {cle(v) for v in crit["villes"]}:
        return False
    if crit.get("types") and a["type"] not in crit["types"]:
        return False
    for champ, seuil, sens in (("loyer", "loyer_max", -1), ("pieces", "pieces_min", 1),
                               ("chambres", "chambres_min", 1), ("surface", "surface_min", 1)):
        limite, valeur = crit.get(seuil), a.get(champ)
        # Une valeur inconnue ne bloque pas l'alerte : mieux vaut une alerte de trop.
        if limite is not None and valeur is not None and (valeur - limite) * sens < 0:
            return False
    if crit.get("meuble") is not None and a.get("meuble") is not None and a["meuble"] != crit["meuble"]:
        return False
    if crit.get("exclure_saisonnier") and a.get("saisonnier"):
        return False
    return True


def fmt(v, suffixe=""):
    return f"{v:g}{suffixe}" if isinstance(v, (int, float)) else "?"


def message(a):
    infos = " · ".join(x for x in (
        fmt(a["pieces"], " p.") if a.get("pieces") else None,
        fmt(a["chambres"], " ch.") if a.get("chambres") else None,
        fmt(a["surface"], " m²") if a.get("surface") else None,
        {True: "meublé", False: "vide"}.get(a.get("meuble")),
    ) if x)
    lignes = [
        f"<b>{fmt(a['loyer'], ' €')}/mois</b> — {html.escape(a['ville'])}",
        f"{html.escape(a['type'].capitalize())} · {infos}" if infos else html.escape(a["type"].capitalize()),
    ]
    if a.get("saisonnier"):
        lignes.append("⚠️ Peut-être saisonnière (à vérifier)")
    lignes.append(f'<a href="{a["url"]}">Voir l\'annonce sur {a["source"]}</a>')
    return "\n".join(lignes)


def telegram(texte, photo=None):
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        print("[alerte, Telegram non configuré]\n" + texte + "\n")
        return
    base = f"https://api.telegram.org/bot{token}/"
    if photo:
        r = httpx.post(base + "sendPhoto", data={"chat_id": chat, "photo": photo, "caption": texte, "parse_mode": "HTML"}, timeout=30)
        if r.status_code == 200:
            return
    r = httpx.post(base + "sendMessage", data={"chat_id": chat, "text": texte, "parse_mode": "HTML"}, timeout=30)
    if r.status_code != 200:
        print("Échec Telegram :", r.status_code, r.text[:200], file=sys.stderr)


# --- Programme principal ---------------------------------------------------

def main():
    ancien = json.loads(FICHIER_DONNEES.read_text(encoding="utf-8")) if FICHIER_DONNEES.exists() else {}
    anciennes = {a["id"]: a for a in ancien.get("annonces", [])}
    etat_sources = ancien.get("sources", {})
    criteres = json.loads(FICHIER_CRITERES.read_text(encoding="utf-8"))
    t = maintenant()

    alertes, nouvelles = [], {}
    with httpx.Client(headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9"}, timeout=40) as client:
        for nom, fonction in SOURCES.items():
            etat = etat_sources.setdefault(nom, {"echecs": 0})
            try:
                trouvees = fonction(client)
            except Exception as e:  # une source en panne ne doit pas bloquer les autres
                etat["echecs"] = etat.get("echecs", 0) + 1
                etat["erreur"] = f"{type(e).__name__}: {e}"[:200]
                print(f"[{nom}] ÉCHEC : {etat['erreur']}", file=sys.stderr)
                if etat["echecs"] == ALERTE_APRES_ECHECS:
                    telegram(f"⚠️ La source <b>{nom}</b> ne répond plus depuis {ALERTE_APRES_ECHECS} vérifications.\n{html.escape(etat['erreur'])}")
                continue
            premiere_fois = "ok_le" not in etat
            if etat.get("echecs", 0) >= ALERTE_APRES_ECHECS:
                telegram(f"✅ La source <b>{nom}</b> répond de nouveau.")
            etat.update(echecs=0, erreur=None, ok_le=t, nb=len(trouvees))
            print(f"[{nom}] {len(trouvees)} annonces")
            for a in trouvees:
                vieille = anciennes.get(a["id"])
                a["vu_le"] = vieille["vu_le"] if vieille else t
                a["actif"] = True
                if vieille and vieille.get("loyer") and a.get("loyer") and a["loyer"] < vieille["loyer"]:
                    a["ancien_loyer"] = vieille["loyer"]
                elif vieille and vieille.get("ancien_loyer"):
                    a["ancien_loyer"] = vieille["ancien_loyer"]
                nouvelles[a["id"]] = a
                if not vieille and not premiere_fois and correspond(a, criteres):
                    alertes.append(a)

    # Annonces disparues : gardées « retirées » quelque temps, sauf si leur source a échoué.
    sources_ok = {n for n, e in etat_sources.items() if e.get("ok_le") == t}
    limite = (datetime.fromisoformat(t) - GARDER_RETIREES).isoformat()
    for id_, a in anciennes.items():
        if id_ in nouvelles:
            continue
        source_ok = id_.split(":")[0] in sources_ok
        if source_ok and a.get("actif"):
            a = {**a, "actif": False, "retire_le": t}
        if a.get("actif") or a.get("retire_le", t) > limite:
            nouvelles[id_] = a

    annonces = sorted(nouvelles.values(), key=lambda a: (a.get("publie") or a["vu_le"]), reverse=True)
    donnees = {"maj": t, "sources": etat_sources, "annonces": annonces}

    # On n'écrit que si quelque chose a vraiment changé (évite un commit toutes les 10 min).
    comparable = lambda d: json.dumps({**d, "maj": None, "sources": {k: {**v, "ok_le": None} for k, v in d.get("sources", {}).items()}}, sort_keys=True)
    if comparable(donnees) != comparable(ancien):
        FICHIER_DONNEES.parent.mkdir(parents=True, exist_ok=True)
        FICHIER_DONNEES.write_text(json.dumps(donnees, ensure_ascii=False, indent=1), encoding="utf-8")
        print("Données mises à jour.")

    for a in alertes:
        telegram("🏠 <b>Nouvelle annonce</b>\n" + message(a), a.get("photo"))
    print(f"{len(alertes)} alerte(s) envoyée(s).")


if __name__ == "__main__":
    main()
