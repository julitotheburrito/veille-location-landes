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
try:
    from zoneinfo import ZoneInfo
    PARIS = ZoneInfo("Europe/Paris")
except Exception:  # Windows sans le paquet tzdata
    PARIS = timezone(timedelta(hours=1))
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


def details_bienici(client, a):
    """Agence, téléphone et disponibilité, tels qu'affichés sur la fiche de l'annonce."""
    r = client.get("https://www.bienici.com/realEstateAd.json", params={"id": a["id"].split(":", 1)[1]})
    r.raise_for_status()
    d = r.json()
    contact = d.get("contactRelativeData") or {}
    dispo = d.get("availableDate")
    return {
        "agence": contact.get("agencyNameToDisplay") or contact.get("contactNameToDisplay"),
        "tel": contact.get("phoneToDisplay"),
        "dispo": dispo[:10] if dispo and not dispo.startswith("1970") else None,
    }


# Orpi ne publie pas sur Bien'ici. Ses pages « location-immobiliere-<commune> » montrent aussi
# les communes voisines : ces 6 pages couvrent tout le secteur (les autres redirigent vers les Landes).
PAGES_ORPI = ["soustons", "leon", "vieux-boucau-les-bains", "linxe", "messanges", "seignosse"]


def texte(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def source_orpi(client):
    annonces, reponses = {}, 0
    for page in PAGES_ORPI:
        r = client.get(f"https://www.orpi.com/location-immobiliere-{page}/")
        if r.status_code != 200:  # commune sans page : Orpi redirige
            continue
        reponses += 1
        for carte in re.split(r'data-oncrawl="estate-card"', r.text)[1:]:
            ref = re.search(r'data-reference="([0-9a-f-]+)"', carte)
            lien = re.search(r'href="(/annonce-location-(appartement|maison)-[^"?]+)', carte)
            if not (ref and lien) or re.search(r'price-tag[^>]*>\s*Lou', carte):  # « Loué »
                continue
            champ = lambda nom: texte((re.search(r'data-oncrawl="' + nom + r'"[^>]*>(.*?)</span>', carte, re.S) or [None, ""])[1])
            ville = NOM_PAR_CLE.get(cle(champ("estate-location")))
            prix = re.sub(r"[^\d]", "", champ("estate-price"))
            if not ville or not prix:
                continue
            tags = [texte(t) for t in re.findall(r'data-oncrawl="estate-tag"[^>]*>(.*?)</li>', carte, re.S)]
            chambres = next((nombre(t) for t in tags if "chambre" in t), None)
            pieces = nombre(champ("estate-rooms"))
            surface = re.search(r"([\d,]+)\s*m", champ("estate-surface"))
            photo = re.search(r'data-src="([^"]+)"', carte)
            alt = html.unescape((re.search(r'alt="([^"]*)"', carte) or [None, ""])[1])
            annonces[ref.group(1)] = {
                "id": "orpi:" + ref.group(1),
                "source": "Orpi",
                "url": "https://www.orpi.com" + lien.group(1),
                "titre": f"{lien.group(2).capitalize()} {int(pieces)} pièces" if pieces else lien.group(2).capitalize(),
                "ville": ville,
                "cp": None,
                "type": lien.group(2),
                "loyer": int(prix),
                "pieces": int(pieces) if pieces else None,
                "chambres": int(chambres) if chambres else None,
                "surface": float(surface.group(1).replace(",", ".")) if surface else None,
                "meuble": True if any("meubl" in t.lower() for t in tags) else None,
                "photo": html.unescape(photo.group(1)) if photo else None,
                "publie": None,
                "saisonnier": est_saisonnier(alt),
                "extrait": alt[:220],
            }
    if not reponses:
        raise RuntimeError("aucune page Orpi n'a répondu")
    return list(annonces.values())


def details_orpi(client, a):
    """Chambres, meublé, date de mise en ligne, agence et téléphone, lus sur la fiche Orpi."""
    r = client.get(a["url"])
    r.raise_for_status()
    t = r.text
    val = lambda nom: (re.search(nom + r':\s*"([^"]*)"', t) or [None, ""])[1]
    tel = re.search(r'href="tel:([^"]+)"', t)
    agence = val("agenceNom")
    d = {
        "agence": "Orpi " + agence.replace("-", " ").title() if agence else "Orpi",
        "tel": tel.group(1).strip() if tel else None,
        "dispo": None,
        "publie": val("dateCreation") or None,
    }
    if val("nbChambres").isdigit():
        d["chambres"] = int(val("nbChambres"))
    if val("meuble") in ("0", "1"):
        d["meuble"] = val("meuble") == "1"
    return d


# PAP (derrière Cloudflare) refuse les programmes depuis novembre 2026 : en pause.
# On ne contourne pas ce blocage ; remettre "pap": source_pap s'il se lève.
SOURCES = {"bienici": source_bienici, "orpi": source_orpi}
DETAILS = {"bienici": details_bienici, "orpi": details_orpi}
CHAMPS_DETAILS = ("agence", "tel", "dispo")  # champs repris des fiches déjà lues (+ ceux listés dans a["details"])
DETAILS_PAR_PASSAGE = 25  # fiches détaillées demandées au plus par vérification (politesse)


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


def meme_bien(a, b):
    """Même commune, loyer à 5 € près, surface à 1,5 m² près : sans doute le même bien publié deux fois."""
    return (a["source"] != b["source"] and cle(a.get("ville")) == cle(b.get("ville"))
            and a.get("loyer") and b.get("loyer") and abs(a["loyer"] - b["loyer"]) <= 5
            and a.get("surface") and b.get("surface") and abs(a["surface"] - b["surface"]) <= 1.5)


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
    if a.get("dispo"):
        jour = datetime.fromisoformat(a["dispo"]).date()
        lignes.append("Disponible tout de suite" if jour <= datetime.now(PARIS).date() else "Disponible le " + jour.strftime("%d/%m/%Y"))
    if a.get("saisonnier"):
        lignes.append("⚠️ Peut-être saisonnière (à vérifier)")
    if a.get("tel") or a.get("agence"):
        # Telegram rend le numéro cliquable : on appelle directement depuis la notification.
        lignes.append("📞 " + " — ".join(html.escape(x, quote=False) for x in (a.get("tel"), a.get("agence")) if x))
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


def bilan(annonces, sources, criteres, t):
    actives = [a for a in annonces if a.get("actif")]
    ok = [a for a in actives if correspond(a, criteres)]
    semaine = (datetime.fromisoformat(t) - timedelta(days=7)).isoformat()
    neuves = [a for a in actives if a["vu_le"] > semaine]
    pannes = [n for n, s in sources.items() if s.get("echecs")]
    lignes = [
        "✅ <b>Veille OK</b> — bilan du lundi",
        f"{len(actives)} annonces suivies, dont {len(ok)} qui collent à tes critères.",
        f"{len(neuves)} nouvelle(s) repérée(s) cette semaine.",
        "⚠️ Source en panne : " + ", ".join(pannes) if pannes else "Sources : toutes répondent.",
        "Pas de bilan lundi prochain = la veille est arrêtée : préviens-moi.",
    ]
    return "\n".join(lignes)


# --- Programme principal ---------------------------------------------------

def main():
    ancien = json.loads(FICHIER_DONNEES.read_text(encoding="utf-8")) if FICHIER_DONNEES.exists() else {}
    anciennes = {a["id"]: a for a in ancien.get("annonces", [])}
    etat_sources = ancien.get("sources", {})
    criteres = json.loads(FICHIER_CRITERES.read_text(encoding="utf-8"))
    t = maintenant()

    alertes, baisses, nouvelles = [], [], {}
    budget_details = DETAILS_PAR_PASSAGE
    deja_vues = [a for a in anciennes.values() if a.get("actif")]
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
                baisse = bool(vieille and vieille.get("loyer") and a.get("loyer") and a["loyer"] < vieille["loyer"])
                if baisse:
                    a["ancien_loyer"] = vieille["loyer"]
                elif vieille and vieille.get("ancien_loyer"):
                    a["ancien_loyer"] = vieille["ancien_loyer"]
                # Fiche détaillée (agence, téléphone…) : demandée une seule fois par annonce.
                if vieille and "agence" in vieille:
                    cles = vieille.get("details") or CHAMPS_DETAILS
                    a.update({k: vieille.get(k) for k in cles}, details=list(cles))
                elif nom in DETAILS and budget_details > 0:
                    budget_details -= 1
                    try:
                        fiche = DETAILS[nom](client, a)
                        a.update(fiche, details=sorted(fiche))
                    except Exception as e:  # sans fiche, l'alerte part quand même
                        print(f"[{nom}] fiche {a['id']} indisponible : {e}", file=sys.stderr)
                nouvelles[a["id"]] = a
                if not vieille and not premiere_fois and correspond(a, criteres):
                    if any(meme_bien(a, b) for b in deja_vues):
                        print(f"[{nom}] doublon ignoré : {a['id']}")
                    else:
                        alertes.append(a)
                elif baisse and correspond(a, criteres):
                    baisses.append(a)
                deja_vues.append(a)

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
    donnees = {"maj": t, "communes": [c[0] for c in COMMUNES], "sources": etat_sources, "annonces": annonces,
               "sante": ancien.get("sante")}

    # Bilan du lundi matin : s'il n'arrive pas, c'est que la veille est arrêtée.
    # Il est aussi enregistré dans les données, ce qui fait un commit par semaine
    # (GitHub suspend les tâches planifiées d'un dépôt inactif depuis 60 jours).
    local = datetime.now(PARIS)
    semaine = "{}-S{:02d}".format(*local.isocalendar()[:2])
    if local.weekday() == 0 and local.hour >= 9 and donnees["sante"] != semaine:
        donnees["sante"] = semaine
        telegram(bilan(annonces, etat_sources, criteres, t))

    # On n'écrit que si quelque chose a vraiment changé (évite un commit toutes les 10 min).
    comparable = lambda d: json.dumps({**d, "maj": None, "sources": {k: {**v, "ok_le": None} for k, v in d.get("sources", {}).items()}}, sort_keys=True)
    if comparable(donnees) != comparable(ancien):
        FICHIER_DONNEES.parent.mkdir(parents=True, exist_ok=True)
        FICHIER_DONNEES.write_text(json.dumps(donnees, ensure_ascii=False, indent=1), encoding="utf-8")
        print("Données mises à jour.")

    for a in alertes:
        telegram("🏠 <b>Nouvelle annonce</b>\n" + message(a), a.get("photo"))
    for a in baisses:
        telegram(f"📉 <b>Baisse de loyer</b> : {fmt(a['ancien_loyer'], ' €')} → {fmt(a['loyer'], ' €')}\n" + message(a), a.get("photo"))
    print(f"{len(alertes)} alerte(s), {len(baisses)} baisse(s) envoyée(s).")


if __name__ == "__main__":
    main()
