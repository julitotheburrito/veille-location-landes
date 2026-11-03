"""Applique les critères d'alerte envoyés depuis l'interface (via une « issue » GitHub).

Lit le bloc ```json du texte de l'issue (variable ISSUE_BODY), vérifie chaque valeur,
réécrit docs/criteres.json et confirme sur Telegram. Refuse tout ce qui n'est pas prévu.
"""
import json
import os
import re
import sys

from collect import COMMUNES, FICHIER_CRITERES, cle, telegram

NOMS = {cle(nom): nom for nom, *_ in COMMUNES}


def entier(v, nom, maxi):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= maxi:
        raise ValueError(f"{nom} doit être un nombre entre 1 et {maxi}")
    return int(v)


def valider(brut):
    villes = brut.get("villes")
    if not isinstance(villes, list) or not villes:
        raise ValueError("au moins une commune")
    inconnues = [v for v in villes if cle(v) not in NOMS]
    if inconnues:
        raise ValueError("communes inconnues : " + ", ".join(map(str, inconnues)))
    types = brut.get("types")
    if not isinstance(types, list) or not types or not set(types) <= {"maison", "appartement"}:
        raise ValueError("types : maison et/ou appartement")
    if brut.get("meuble") not in (None, True, False):
        raise ValueError("meuble : null, true ou false")
    if not isinstance(brut.get("exclure_saisonnier"), bool):
        raise ValueError("exclure_saisonnier : true ou false")
    return {
        "_aide": "Critères des ALERTES Telegram. null = pas de limite. meuble : null (indifférent), true (meublé seulement), false (vide seulement).",
        "villes": [NOMS[cle(v)] for v in dict.fromkeys(villes)],
        "types": [t for t in ("maison", "appartement") if t in types],
        "loyer_max": entier(brut.get("loyer_max"), "loyer_max", 20000),
        "pieces_min": entier(brut.get("pieces_min"), "pieces_min", 20),
        "chambres_min": entier(brut.get("chambres_min"), "chambres_min", 20),
        "surface_min": entier(brut.get("surface_min"), "surface_min", 2000),
        "meuble": brut.get("meuble"),
        "exclure_saisonnier": brut["exclure_saisonnier"],
    }


def resume(c):
    morceaux = [
        ", ".join(c["villes"]),
        " et ".join(t + "s" for t in c["types"]),
        f"loyer ≤ {c['loyer_max']} €" if c["loyer_max"] else None,
        f"{c['pieces_min']} pièces et +" if c["pieces_min"] else None,
        f"{c['chambres_min']} chambres et +" if c["chambres_min"] else None,
        f"{c['surface_min']} m² et +" if c["surface_min"] else None,
        {True: "meublé", False: "non meublé"}.get(c["meuble"]),
        "à l'année seulement" if c["exclure_saisonnier"] else "saisonnier inclus",
    ]
    return "\n".join("• " + m for m in morceaux if m)


def main():
    corps = os.environ.get("ISSUE_BODY", "")
    bloc = re.search(r"```json\s*(\{.*?\})\s*```", corps, re.S)
    try:
        if not bloc:
            raise ValueError("bloc ```json introuvable")
        criteres = valider(json.loads(bloc.group(1)))
    except (ValueError, json.JSONDecodeError) as e:
        print(f"Critères refusés : {e}")
        telegram(f"❌ Critères d'alerte refusés : {e}")
        sys.exit(1)
    FICHIER_CRITERES.write_text(json.dumps(criteres, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    texte = resume(criteres)
    print(texte)
    telegram("✅ <b>Critères d'alerte mis à jour</b>\n" + texte)


if __name__ == "__main__":
    main()
