"""Configure les alertes Telegram, une seule fois.

1. Sur Telegram, écris à @BotFather : /newbot, choisis un nom -> il te donne un « token ».
2. Ouvre ton nouveau bot et envoie-lui « /start ».
3. Lance : python scraper/telegram_setup.py   (depuis le dossier du projet)

Le token est collé ici, envoyé directement dans les « secrets » GitHub du dépôt
avec la commande gh, et n'est jamais écrit dans un fichier.
"""
import subprocess
import sys

import httpx

# input() plutôt que getpass : le collage (Ctrl+V / clic droit) ne passe pas toujours dans un champ masqué.
token = input("Colle le token du bot puis Entrée : ").strip().strip('"\'')
print(f"Token reçu : {len(token)} caractères (normal : environ 46).")
base = f"https://api.telegram.org/bot{token}/"

moi = httpx.get(base + "getMe", timeout=20).json()
if not moi.get("ok"):
    sys.exit("Token refusé par Telegram. Si tu as fait /revoke, utilise le NOUVEAU token donné par @BotFather.")

maj = httpx.get(base + "getUpdates", timeout=20).json().get("result", [])
chats = {u["message"]["chat"]["id"] for u in maj if "message" in u}
if not chats:
    sys.exit(f"Aucun message reçu. Envoie « /start » à @{moi['result']['username']} puis relance.")
chat_id = str(sorted(chats)[-1])

httpx.post(base + "sendMessage", data={"chat_id": chat_id, "text": "✅ Alertes locations Landes : c'est branché."}, timeout=20)
print(f"Message de test envoyé (chat {chat_id}).")

for nom, valeur in (("TELEGRAM_TOKEN", token), ("TELEGRAM_CHAT_ID", chat_id)):
    subprocess.run(["gh", "secret", "set", nom], input=valeur, text=True, check=True)
print("Secrets GitHub enregistrés. Les alertes partiront à la prochaine vérification.")
