# Image Lab — choix du modèle et atelier Qwen

Conserver l'identité et la galerie privée du site. Donner le premier rôle aux images et rendre le changement de modèle immédiatement lisible.

- Palette : fond #0D0F14, panneaux #161A22, bordures #2A313D, texte #E7ECF3, secondaire #9CA7B8, accent bleu #809BFF. Le vert des GPU indique uniquement une machine démarrée ; le texte distingue préparation, disponibilité et calcul.
- Typographie : police système actuelle ; titres 24/20 px, boutons 14 px, aides 12 px. Alignement à gauche, phrases courtes.
- Structure : en-tête compact compte/langue ; trois choix de modèle avec avantages ; synthèse des fonctions disponibles ; onglets créer/éditer/galerie ; colonne réglages à gauche, prompt et résultats à droite. Dans Qwen, bandeau A10/A100, arrêt automatique et file persistante.
- État : prompt et références conservés lors d'un changement de modèle. Réglages mémorisés par modèle. Les fonctions non prises en charge restent visibles, désactivées au clavier comme à la souris et accompagnées d'une explication. Les requêtes ne transmettent que les réglages compatibles.
- Mobile : modèles sur trois lignes compactes, GPU en pile, réglages en panneau accessible, commande de génération en bas. Aucun tableau large.

Revue avant construction : éviter un second atelier séparé, des réglages génériques qui seraient ignorés silencieusement, ou une promesse de démarrage A10 sans validation. La galerie et les images générées restent la surface principale. La file et les voyants présentent l'état réel, sans durée inventée. Qwen reçoit le prompt saisi sans filtre ajouté.
