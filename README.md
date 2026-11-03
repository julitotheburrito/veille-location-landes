# Veille locations à l'année — côte sud des Landes

Surveille les locations (maisons, appartements) à Moliets-et-Maa, Soustons, Vieux-Boucau,
Linxe, Léon et leurs voisines. Toutes les 10 minutes, GitHub vérifie les annonces et envoie
une alerte Telegram pour chaque nouveauté qui colle aux critères.

- **Interface** : la page GitHub Pages du dépôt (dossier `docs/`), filtres par commune, type,
  loyer, pièces, chambres, surface, meublé ; repère les locations « de septembre à juin ».
- **Critères des alertes** : `docs/criteres.json` (bouton « Modifier les critères d'alerte »
  dans l'interface).
- **Sources** : Bien'ici (la plupart des agences) + Orpi (absent de Bien'ici) + ParuVendu (dont des particuliers). PAP est en pause (il bloque
  les programmes) ; Leboncoin et SeLoger aussi : utiliser leurs propres alertes.

## Mise en route des alertes

1. Telegram → @BotFather → `/newbot` → note le token.
2. Envoie `/start` à ton bot.
3. Dans ce dossier : `python scraper/telegram_setup.py`

## Lancer à la main

```
pip install -r scraper/requirements.txt
python scraper/collect.py
```
