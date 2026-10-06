# Image Lab

Atelier Azure GPT-image avec génération, édition et galerie privée par compte Google autorisé.

## Qwen Image 2.1 et choix du modèle

Qwen Image 2.1 utilise l'A100 Azure existante, avec ses poids BF16 et la même archive vérifiée que les générations d'avatars. L'interface conserve le prompt et les références lors d'un changement de modèle, et mémorise séparément les réglages de chaque modèle. La matrice `capabilities.py` pilote les contrôles disponibles et la validation serveur. Les fonctions incompatibles sont désactivées ; celles de Qwen se trouvent dans ses réglages propres.

Qwen accepte jusqu'à huit références entières, une à six images par demande, de 256 à 2048 pixels de côté par multiples de 32, une seed, les étapes et le guidage. Le prompt négatif agit avec un guidage supérieur à 1. Les prompts sont transmis tels quels, sans filtre de contenu ou réécriture ajouté par Image Lab. Le modèle ne propose pas ici de masque, de transparence native ni de génération 4K native.

Les boutons GPU et voyants utilisent l'état Azure réel. Le voyant vert signifie que la machine est démarrée ; le texte précise si Qwen est prêt ou si une autre tâche l'utilise. L'A10 est visible mais son démarrage pour Qwen est désactivé : l'installation BF16 utilise plus de 32 Go de mémoire GPU, contre 24 Go sur l'A10. Une configuration avec déport en RAM demanderait une validation distincte. L'application ne stoppe pas une tâche externe pour s'approprier le GPU.

La file Qwen et les références sont persistées sous `/data/qwen`. Fermer la page n'annule pas une génération. Chaque demande reste isolée par propriétaire, reçoit une clé d'idempotence et conserve son reçu distant avant soumission. La reprise du suivi ne déclenche pas un second appel. Les sorties sont ajoutées à la galerie privée et restent téléchargeables avec leurs réglages. Les annulations sont coopératives. L'arrêt automatique intervient après 15 minutes d'inactivité, valeur réglable ; l'arrêt manuel est refusé tant qu'une demande est en cours ou en attente.

Le serveur utilise `QWEN_AZURE_TENANT_ID`, `QWEN_AZURE_CLIENT_ID`, `QWEN_AZURE_CLIENT_SECRET` via une identité dédiée, limitée à la ressource A100, la lecture de l'A10 et l'échange Blob privé. Aucun secret n'est envoyé au navigateur. Sans ces variables, le développement local utilise la connexion Azure CLI. L'identité de production créée le 6 octobre 2026 possède un secret d'un an : le renouveler dans Coolify avant expiration. `QWEN_ENGINE_DISABLED=1` désactive le worker uniquement pour les tests d'interface locaux.

## Images et galerie

Les nouvelles générations et éditions conservent leurs octets originaux, une miniature WebP et leurs métadonnées (prompt, modèle, dimensions, date). L'onglet **Ma galerie** propose la recherche, l'agrandissement, le téléchargement de l'original et la reprise en édition. **Supprimer** est disponible sur chaque image et dans l'aperçu agrandi, avec confirmation. La suppression retire l'original, la miniature et l'entrée de galerie du compte connecté ; elle ne touche pas les références ni l'historique des demandes Qwen. Les fichiers sont servis par des routes authentifiées qui vérifient leur propriétaire. La route de suppression vérifie aussi un jeton de session et l'origine de la requête.

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
