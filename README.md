# Image Lab

Atelier Azure GPT-image avec génération, édition et galerie privée par compte Google autorisé.

## Qwen Image 2.1 et choix du modèle

Qwen Image 2.1 utilise l'A100 Azure du même environnement, avec ses poids BF16 vérifiés. L'interface conserve le prompt et les références lors d'un changement de modèle, et mémorise séparément les réglages de chaque modèle. La matrice `capabilities.py` pilote les contrôles disponibles et la validation serveur. Les fonctions incompatibles sont désactivées ; celles de Qwen se trouvent dans ses réglages propres.

Qwen accepte jusqu'à huit références entières, une à six images par demande, de 256 à 2048 pixels de côté par multiples de 32, une seed, les étapes et le guidage. Le prompt négatif agit avec un guidage supérieur à 1. Les prompts sont transmis tels quels, sans filtre de contenu ou réécriture ajouté par Image Lab. Le modèle ne propose pas ici de masque, de transparence native ni de génération 4K native.

Les boutons GPU et voyants utilisent l'état Azure réel. Le voyant vert signifie que la machine est démarrée ; le texte précise si Qwen est prêt ou si une autre tâche l'utilise. L'A10 est visible mais son démarrage pour Qwen est désactivé : l'installation BF16 utilise plus de 32 Go de mémoire GPU, contre 24 Go sur l'A10. Une configuration avec déport en RAM demanderait une validation distincte. L'application ne stoppe pas une tâche externe pour s'approprier le GPU.

La file Qwen et les références sont persistées sous `/data/qwen`. Fermer la page n'annule pas une génération. Chaque demande reste isolée par propriétaire, reçoit une clé d'idempotence et conserve son reçu distant avant soumission. La reprise du suivi ne déclenche pas un second appel. Les sorties sont ajoutées à la galerie privée et restent téléchargeables avec leurs réglages. Les annulations sont coopératives. L'arrêt automatique intervient après 15 minutes d'inactivité, valeur réglable ; l'arrêt manuel est refusé tant qu'une demande est en cours ou en attente.

Le serveur utilise `QWEN_AZURE_TENANT_ID`, `QWEN_AZURE_CLIENT_ID`, `QWEN_AZURE_CLIENT_SECRET` via une identité dédiée, limitée à la ressource A100, la lecture de l'A10 et l'échange Blob privé. Aucun secret n'est envoyé au navigateur. Sans ces variables, le développement local utilise la connexion Azure CLI. L'identité de production créée le 6 octobre 2026 possède un secret d'un an : le renouveler dans Coolify avant expiration. `QWEN_ENGINE_DISABLED=1` désactive le worker uniquement pour les tests d'interface locaux.

### Cache permanent Qwen

Image Lab pilote désormais l'application Container Apps `qwen-image-a100`, révision `qwen-image-a100--cache-20261006`, dans `cae-retopo-swe`, groupe `rg-retopo-prod`. Cette application dédiée évite de changer le template partagé de `blender-a100`, dont les révisions et traitements d'avatars restent indépendants. Image Lab vérifie aussi les révisions actives de `blender-a100` et attend quand un traitement d'avatars utilise le GPU. Le quota GPU de l'environnement reste partagé.

Le partage SMB classique `qwen-image-cache` du compte `stastraa1003db80e7b` est en Standard LRS, niveau Hot, quota 80 GiB, dans Sweden Central. La définition de stockage homonyme de l'environnement le monte sous `/mnt/qwen-cache`. Le coût porte sur l'espace occupé et les opérations, pas sur le quota. Environ 36,8 Go sont conservés : poids décompressés de 33,1 Go et environnement Python compressé en huit archives totalisant environ 3,7 Go. Le tarif catalogue Hot LRS relevé le 6 octobre 2026 est de 0,0271 USD/Go/mois, soit environ 1 USD/mois de capacité, hors métadonnées, opérations, taxes et remises. Ce cache ne conserve ni RAM ni VRAM et n'élimine pas le délai de provisionnement Azure.

Le dossier versionné par l'empreinte de l'archive contient les poids, `venv.tar.gz` et un `manifest.json` publié après contrôle SHA-256 de tous les fichiers. À chaque nouvelle réplique, les poids sont copiés en huit lectures parallèles vers le disque local et vérifiés pendant la copie. Les huit archives de l'environnement Python sont copiées, vérifiées et décompressées en parallèle localement, afin d'éviter les petites lectures SMB lors de l'import. Un marqueur local évite toute nouvelle copie pendant la vie de la même réplique. En cas de cache absent ou altéré, la préparation échoue explicitement ; elle ne télécharge pas silencieusement un autre modèle. L'ancienne archive Blob est conservée pour la récupération administrative.

Le conteneur utilise l'identité managée `image-lab-qwen-a100`, avec `AcrPull` sur le registre existant uniquement. La clé du partage est stockée dans la définition de montage Azure, jamais dans Git ni dans le navigateur. L'identité de service Image Lab conserve ses droits limités de contrôle sur les deux applications et ne possède pas de droit de modification des templates. Ne pas supprimer le partage lors d'un arrêt GPU : seules les révisions sont désactivées. Renouveler aussi la clé dans la définition de montage après une rotation des clés du compte de stockage.

## Images et galerie

Les nouvelles générations et éditions conservent leurs octets originaux, une miniature WebP et leurs métadonnées (prompt, modèle, dimensions, date). L'onglet **Ma galerie** propose la recherche, l'agrandissement, le téléchargement de l'original et la reprise en édition. **Supprimer** est disponible sur chaque image et dans l'aperçu agrandi, avec confirmation. La suppression retire l'original, la miniature et l'entrée de galerie du compte connecté ; elle ne touche pas les références ni l'historique des demandes Qwen. Les fichiers sont servis par des routes authentifiées qui vérifient leur propriétaire. La route de suppression vérifie aussi un jeton de session et l'origine de la requête.

Les anciennes versions ne conservaient les images que dans la page ouverte. **Ajouter des images** permet de récupérer celles téléchargées précédemment. Une importation répétée du même fichier par le même compte ne crée pas de doublon. Les sources d'édition ne sont pas archivées automatiquement ; les résultats le sont.

Lorsqu'une même personne utilise plusieurs comptes Google vérifiés, l'exploitant peut réunir explicitement leurs galeries avec `ImageStore.link_owners`. Les alias sont conservés dans SQLite ; lecture, nouvelles générations, imports et suppression utilisent le propriétaire commun. Les identifiants, fichiers et dates des images sont préservés. Aucune association n'est déduite automatiquement des noms ou des adresses, et aucune route publique ne permet de relier des comptes. Les comptes Gmail et Metagora de Johann ont été réunis le 6 octobre 2026 ; les autres utilisateurs restent isolés.

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
