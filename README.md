# Image Lab

Atelier Azure GPT-image avec génération, édition et galerie privée par compte Google autorisé.

## Images et galerie

Les nouvelles générations et éditions conservent leurs octets originaux, une miniature WebP et leurs métadonnées (prompt, modèle, dimensions, date). L'onglet **Ma galerie** propose la recherche, l'agrandissement, le téléchargement de l'original et la reprise en édition. Les fichiers sont servis par des routes authentifiées qui vérifient leur propriétaire.

Les anciennes versions ne conservaient les images que dans la page ouverte. **Ajouter des images** permet de récupérer celles téléchargées précédemment. Une importation répétée du même fichier par le même compte ne crée pas de doublon. Les sources d'édition ne sont pas archivées automatiquement ; les résultats le sont.

## Stockage de production

- Volume physique : `/mnt/HC_Volume_106989825/projects/image-lab`.
- Bind mount protégé sur l'hôte : `/data/image-lab`.
- Stockage persistant Coolify de `image-lab` : hôte `/data/image-lab` vers conteneur `/data`.
- `IMAGE_STORAGE_DIR=/data` et `IMAGE_STORAGE_REQUIRED=1` sont définis dans l'image Docker.
- Le fichier `.image-lab-storage` doit contenir `image-lab-storage-v1` et être créé sur le vrai volume par l'exploitant. L'application ne le crée jamais en mode obligatoire.
- Les originaux et miniatures occupent des sous-dossiers UUID ; l'index est `gallery.sqlite3`. Sauvegarder les fichiers **et** une copie SQLite cohérente. Le volume additionnel n'est pas inclus dans les snapshots du disque système Hetzner.

Les unités systemd `.mount` et `.automount` protègent le point de montage et le rendent disponible avant Docker. En cas de volume absent, ne pas créer un marqueur sur un dossier de remplacement. Remettre le montage en état, puis redémarrer uniquement Image Lab.

Avant un appel Azure, l'application vérifie la disponibilité de l'écriture et une marge d'espace libre. Si le stockage échoue après la génération, les originaux sont tout de même renvoyés dans la page avec une invitation à les télécharger immédiatement. La galerie reste disponible pendant un appel Azure, exécuté hors de la boucle HTTP.

## Développement et validation

Installer `requirements.txt`, puis lancer `python -m uvicorn app:app --host 127.0.0.1 --port 8000`. Sans configuration spécifique, les fichiers locaux sont dans le dossier ignoré `data`. La configuration OAuth/Azure figure dans `.env.example` ; ne jamais versionner `.env`.

Tests : installer `httpx`, puis `python -m unittest discover -s tests -v`. Ils utilisent des images synthétiques et un fournisseur Azure simulé, sans appel facturé. Ils couvrent l'intégrité des octets, l'isolation entre comptes, la pagination, les imports, génération/édition et les défaillances de stockage.
