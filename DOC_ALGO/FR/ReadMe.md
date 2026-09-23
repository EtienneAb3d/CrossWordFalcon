# Comment CrossWordFalcon construit une grille

Ce document explique, en termes simples mais complets, l'algorithme de
génération de grilles de `backend/crossword_gen.py` : comment les cases
noires sont posées, comment les mots sont choisis et posés, et comment une
grille bloquée est récupérée plutôt que jetée.

**Comment lire ce document.** Il suit l'**ordre d'exécution** de
l'algorithme : d'abord le principe général ci-dessous (la vue d'ensemble
suffisante pour comprendre de quoi parlent les chapitres), puis un chapitre
par partie. Chaque chapitre commence par une **description générale** de ce
que fait cette partie, avant d'en détailler les **particularités, cas
particuliers et exceptions**.

Le sens exact des termes employés ici (emplacement bloqué, case croisée
bloquée, emplacement écarté, emplacement pauvre, case croisée injouable) est
fixé une fois pour toutes dans `DOC_ALGO/FR/Lexicon.md`.

Cet algorithme existe en deux implémentations identiques : Python
(`backend/crossword_gen.py`, citée tout au long de ce document) et Java
(`backend_java/`, paquet `falcon.gen`, où chaque fonction citée ici a son
équivalent du même nom en camelCase). Ce document les spécifie toutes les
deux ; en Java, les tentatives parallèles d'un palier sont des fils
d'exécution d'un même processus plutôt que des processus séparés, sans
autre différence de comportement.

---

## Principe général

### Le vocabulaire minimal

- Un **emplacement** est une suite de cases blanches consécutives, sur une
  ligne (horizontal) ou une colonne (vertical), destinée à recevoir un mot.
  Toute suite d'au moins **2** cases est un emplacement à remplir et à
  définir ; une case blanche seule entre deux cases noires dans un sens
  n'est qu'une case de passage pour le mot qui la traverse dans l'autre
  sens. Une même case appartient le plus souvent à deux emplacements à la
  fois (un horizontal et un vertical qui se croisent).
- Une **tentative** est une construction complète et indépendante : un
  motif de cases noires, puis une recherche de remplissage sur ce motif.
- Un **palier** est un lot de tentatives menées **en parallèle**, une par
  processus. Le palier réussit dès qu'une de ses tentatives remplit
  entièrement sa grille ; sinon il échoue, et transmet au palier suivant ce
  qu'il a de récupérable.

### Les trois phases d'une tentative

1. **Poser les cases noires** (chapitre 3) — construire le motif noir/blanc
   sur lequel la recherche va travailler.
2. **Remplir les cases blanches avec de vrais mots** (chapitre 4) — une
   recherche par essais successifs avec retour en arrière
   (*backtracking*) : choisir un emplacement, y essayer un mot du
   dictionnaire compatible avec les lettres déjà imposées par les mots
   croisés, puis recommencer sur l'emplacement suivant ; revenir en arrière
   dès qu'un emplacement se retrouve sans aucun mot possible.
3. **Simplifier ce qui a échoué** (chapitre 5) — si la recherche n'a pas
   rempli toute la grille, retirer de cette tentative les mots et les cases
   noires qui bloquent, pour que le palier suivant reparte de ce qui reste
   exploitable plutôt que de zéro.

### La boucle entre paliers

Une génération enchaîne jusqu'à **200 paliers** (`attempts`). Après chaque
palier échoué, le programme choisit comment enchaîner :

- **reprise « telle quelle »** — le motif de chaque tentative est conservé
  à l'identique et seulement débarrassé de ce qui bloque, parce qu'il reste
  au moins un emplacement qui a encore une chance d'aboutir ;
- **nettoyage complet puis motif neuf** — plus aucun emplacement libre n'a
  de chance d'aboutir : on ne garde que les lettres confirmées et on
  régénère un motif de cases noires ;
- **nettoyage profond, puis grille écartée** — une grille nettoyée qui
  reproduit le même état deux nettoyages de suite est nettoyée plus en
  profondeur ; si elle le reproduit une troisième fois, elle seule est
  écartée et remplacée par une grille entièrement vierge, les autres
  poursuivant leur progression.

Le remplissage n'est donc presque jamais recommencé de rien : chaque palier
hérite du contenu réellement confirmé par les précédents.

### La fin de la recherche

Une seule tentative réussie ne suffit pas à conclure : il en faut au moins
**2** sur l'ensemble de la recherche (`MIN_SUCCESSFUL_ATTEMPTS`). Une fois
ce seuil atteint, la meilleure de toutes les réussites est retenue, puis
une dernière passe (chapitre 6) lui retire encore le plus de cases noires
possible pour la densifier. Les définitions de chaque mot sont ensuite
écrites par le modèle de langage (`backend/clues.py`), et la grille est
enregistrée.

### Ce que le joueur voit pendant ce temps

L'interface web affiche en direct un aperçu de la recherche : le motif de
départ, le motif de cases noires obtenu, les meilleures tentatives
échouées avec leurs diagnostics (cases bloquées, écartées, verrouillées),
puis l'état de ces mêmes tentatives après optimisation. Le détail de ces
aperçus est au chapitre 7.

---

## Chapitre 1 — Lancer une génération

Il existe deux façons de construire une grille : la laisser entièrement
construire par le programme, ou la construire soi-même mot à mot avec
l'assistance du programme. Les deux passent par la même page d'accueil et
partagent tout le moteur décrit dans ce document ; seul le déclenchement
diffère.

### Mode tout automatique

C'est le fonctionnement par défaut : le programme construit toute la grille
lui-même, sans aucune intervention manuelle.

1. Configurer la grille avec les options de la page d'accueil (langue,
   taille, difficulté, taux noir, graines, etc.).
2. Choisir un **Mode** de génération parmi Flash/Turbo/Rapide/Moyen/Ultra
   (jamais « Interactif », réservé au mode manuel ci-dessous) — ce choix ne
   fixe qu'un budget de recherche par tentative (voir « Limites de la
   recherche », chapitre 4), pas la qualité du résultat final.
3. Lister éventuellement des mots dans le champ **Thématique** pour
   orienter le choix des mots vers un sujet, et/ou des mots dans le champ
   **Mots Défi (personnalisation)** juste en dessous pour forcer des mots
   précis dans la grille (facultatif ; ces mots ne sont jamais montrés dans
   la Bibliothèque).
4. Cliquer sur **Générer la grille**.

À partir de là tout s'enchaîne sans autre action : les trois phases de
chaque tentative, les paliers successifs, l'optimisation finale, puis
l'écriture des définitions. La grille terminée est automatiquement
enregistrée dans la **Bibliothèque** dès qu'elle est prête — il n'y a pas
de bouton **Publier** à cliquer.

Si aucune grille remplissable n'a été trouvée au bout des 200 paliers, un
bouton **Continuer** relance 200 nouveaux paliers en repartant exactement
de l'état où la recherche s'est arrêtée (motif, cases verrouillées, mots
déjà confirmés) plutôt que d'une grille vierge (`backend/crossword_gen.py`,
`generate_grid` et `_serialize_resume_state`/`_deserialize_resume_state` ;
`backend/app.py`, `POST /api/generate/continue/{job_id}`). Une fois la
grille terminée, un bouton **Recalculer** génère un nouveau jeu de
définitions pour la même grille (une copie ; la grille d'origine n'est
jamais modifiée) sans refaire le placement des mots.

### Mode Interactif (construction manuelle assistée)

Pour construire soi-même une grille mot à mot :

1. Configurer la grille avec les options de la page d'accueil (langue,
   taille, difficulté, taux noir, etc.).
2. Choisir le **Mode** « Interactif ».
3. Lister éventuellement des mots dans le champ **Thématique** et/ou dans
   le champ **Mots Défi**.
4. Cliquer sur **Générer la grille**.

Le même guide est disponible dans l'interface une fois la session
démarrée : le bouton **?** situé à gauche de **Mots** l'ouvre dans un
panneau.

- Placer ses lettres dans la grille. La touche Espace ajoute ou supprime
  une case noire.
- Le bouton **Suivant** fait poser automatiquement **un** mot de plus, en
  tenant compte d'abord d'une éventuelle liste **Mots Défi**, puis d'un
  éventuel glossaire thématique (voir « Le mode Interactif : poser un seul
  mot », chapitre 4). Le bouton **Précédent** revient en arrière, par
  exemple pour faire proposer un autre mot.
- Les outils d'aide : **Dictionnaire**, **Paraphraseur**, et le bouton
  **Mots** qui donne la liste des mots compatibles avec l'emplacement
  sélectionné.
- Deux boutons nettoient la grille sur les zones impossibles, avec ou sans
  retrait des cases noires.
- Le bouton **Impossibles** identifie les zones où plus aucun mot n'est
  possible ; **Stats** (bouton bistable, activé par défaut) affiche en
  permanence, en gris clair dans chaque case encore vide, la lettre
  statistiquement la plus probable à cet endroit ; **Vérifier**
  s'assure que tous les mots posés sont bien dans le dictionnaire et
  possèdent une définition.
- Le bouton **Définitions** génère automatiquement les définitions
  manquantes ; **Proposer une définition** et **Proposer un titre** font
  plusieurs propositions.
- **Finir la grille** (ou **Finir la zone**, si une zone est sélectionnée)
  confie les cases encore vides à la génération automatique. À la fin de ce
  processus, on peut supprimer les mots qui ne conviennent pas, replacer
  des lettres, des cases noires et des définitions, et relancer **Finir la
  grille** autant de fois que nécessaire.
- Penser à sauvegarder. Quand la grille est complète et convient, cliquer
  sur **Publier** : elle se retrouve dans la **Bibliothèque**, où l'on peut
  copier un lien pour la jouer ou l'exporter en PDF.

**Finir la grille / Finir la zone** réutilise exactement le pipeline
automatique décrit dans ce document : chaque case déjà posée devient une
contrainte permanente (`permanent_locked_letters`/`permanent_black_cells`),
et, quand une zone est sélectionnée plutôt que la grille entière, seules
les cases de cette zone doivent être résolues pour que la recherche se
déclare réussie (`required_cells`) — les cases en dehors peuvent rester
vides.

### Grilles bilingues

Une grille peut utiliser deux langues à la fois : les mots horizontaux dans
une première langue, les mots verticaux dans une seconde
(`backend/crossword_gen.py`, `generate_grid`, paramètre
`bilingual_wordlist_path`). Tout ce qui suit fonctionne alors exactement de
la même façon, à une exception près : chaque fois qu'un emplacement a
besoin d'un mot candidat, c'est le dictionnaire de **sa propre direction**
qui est consulté, jamais l'autre — deux dictionnaires complets et
indépendants, un par langue, plutôt qu'un dictionnaire mélangeant les deux
(`DualIndex`/`DualSet`). Sur une grille monolingue, les deux dictionnaires
sont en réalité le même objet : rien ne change.

Chaque mot du résultat final porte sa propre langue (champ `language` de
chaque mot renvoyé par `generate_grid`) — celle de la grille pour un mot
horizontal, la seconde pour un mot vertical. C'est cette information qui
permet à `backend/clues.py` d'écrire chaque définition dans la bonne
langue, et au ChatBot (`backend/chatbot.py`) de donner un indice dans la
langue du mot concerné.

---

## Chapitre 2 — L'organisation d'un palier

Un palier ne fait pas un seul essai : puisqu'une tentative est rapide et
qu'une seule n'occupe pas toute la machine, chaque palier lance **autant de
tentatives indépendantes en parallèle que la machine a de processeurs**,
chacune dans son propre processus (`backend/crossword_gen.py`,
`PARALLEL_ATTEMPTS` ; réglable par la variable
`CROSSWORDFALCON_PARALLEL_ATTEMPTS` de `env.sh`). Chaque tentative a son
propre générateur aléatoire, donc son propre motif et ses propres choix de
mots.

Le palier attend que toutes ses tentatives se terminent, récolte leurs
résultats, et décide de la suite : conclure la recherche si le nombre
minimal de réussites est atteint, sinon transmettre au palier suivant ce
que ces tentatives ont produit (chapitre 5).

Au tout premier palier, il n'existe encore aucune grille de départ
commune : **chaque tentative parallèle construit alors sa propre grille
depuis zéro**, motif compris (`make_pattern`). Ce n'est qu'à partir du
premier échec que les tentatives commencent à partir de grilles héritées du
palier précédent — une grille distincte par tentative, jamais la même
rediffusée à toutes.

### Le pré-chauffage du pool de processus

Avant le tout premier palier, une phase de **pré-chauffage** force les
`PARALLEL_ATTEMPTS` processus du pool à démarrer réellement. Sans elle, un
pool tout juste créé ne démarre ses processus que paresseusement : seule
une poignée est réellement disponible pour les premiers paliers, un même
processus traite alors plusieurs tentatives pendant que d'autres restent
inactifs, et le même numéro de processus apparaît plusieurs fois dans un
même aperçu. Le pré-chauffage soumet exactement `PARALLEL_ATTEMPTS` tâches
factices d'un coup, chacune bloquée dans une barrière de synchronisation
partagée tant que toutes ne sont pas arrivées : un processus qui en attrape
une reste occupé tant que les autres n'ont pas démarré, ce qui force le
pool à en créer un nouveau pour chaque tâche restante. La répartition est
donc parfaitement 1:1 dès le premier palier.

### Une tentative va toujours jusqu'au bout

Une tentative de remplissage va jusqu'à son terme avant que le palier ne se
termine : jusqu'à ce qu'il n'y ait plus aucun emplacement jouable (chaque
emplacement restant est soit assigné, soit signalé injouable), que son
budget de vérifications soit dépassé, ou qu'elle soit interrompue par une
tentative sœur du même palier. La décision « reprise telle quelle » /
« nettoyage complet » n'intervient qu'*après* coup — jamais pour
raccourcir la tentative elle-même. Les processus encore en cours ne sont
jamais tués ni abandonnés.

### Interruption anticipée du lot

Une fraction réglable des tentatives, une fois terminées — qu'elles
réussissent ou qu'elles échouent —, peut interrompre toutes les tentatives
encore en cours pour passer directement au palier suivant
(`generate_grid`, `PALIER_ATTEMPT_INTERRUPT_FRACTION`, et le point de
contrôle correspondant dans `Filler._backtrack`) : le palier n'attendrait
alors plus la tentative la plus lente du lot. Cette fraction est
actuellement fixée à **100 %** : le palier attend donc que *toutes* les
tentatives se terminent d'elles-mêmes, sans interruption anticipée, et la
sélection ci-dessous voit toujours un lot complet.

### Nombre minimal de réussites et réaffectation des processus

Une seule tentative réussie ne suffit jamais à conclure la recherche : il
en faut au moins **2**, cumulées sur l'ensemble de la recherche — un palier
suivant peut donc fournir la deuxième réussite d'un palier précédent qui
n'en avait trouvé qu'une (`generate_grid`, `MIN_SUCCESSFUL_ATTEMPTS`).

Chaque processus libéré par une tentative terminée, réussie ou échouée,
est immédiatement réaffecté à une toute nouvelle tentative — un motif
entièrement neuf tiré depuis zéro, jamais une poursuite de la grille qui
vient de se terminer — plutôt que de rester inactif, tant qu'au moins une
tentative **d'origine** du palier est encore en course. Ces tentatives de
remplacement ne prolongent jamais le palier : elles ne comptent pas comme
« en course » pour l'allongement du budget des autres tentatives
(`_pattern_attempt`, `racing=False`), et elles sont toutes interrompues
dès que chaque tentative d'origine s'est terminée ou a consommé tout son
budget (`attempt_done_event`). Une tentative de remplacement interrompue
rend sa meilleure grille comme n'importe quelle tentative échouée.

Ces tentatives de remplacement donnent une chance de réussite
supplémentaire en exploitant toute la machine, mais un palier rend alors
plus de grilles qu'il n'a de processus. Elles ne sont pas toutes reprises
au palier suivant : sur N processus, seules les **N-1 meilleures** grilles
(score de contenu ci-dessous) y sont conservées, plus **1 grille
nouvelle** repartant de zéro (`_seed_pool`, plafonné à `PARALLEL_ATTEMPTS
- reset_count` ; chapitre 5).

Un palier qui se termine avec 0 ou 1 réussite en main ne conclut donc pas :
ses tentatives échouées suivent la mécanique de reprise du chapitre 5, et
l'éventuelle réussite unique reste mémorisée pour être comparée à celles
des paliers suivants. Si la limite de 200 paliers est atteinte sans qu'une
deuxième réussite n'ait jamais été trouvée, l'unique réussite obtenue est
tout de même retenue plutôt que de déclarer un échec total.

### Choisir la meilleure réussite

Une fois le seuil atteint, la sélection porte sur **toutes** les réussites
trouvées jusque-là dans la recherche, quel que soit le palier qui les a
produites — un cas courant, pas une exception : un même palier en fournit
souvent plusieurs à la fois. Ce n'est ni la première trouvée, ni celle qui
semble la meilleure avant optimisation qui est retenue : **chacune** est
d'abord réellement optimisée (sa propre passe de minimisation des cases
noires — chapitre 6 —, sur sa propre copie de grille, jamais sur celle
d'une autre tentative), puis c'est celle qui a le **moins de cases noires
une fois optimisée** qui gagne. À égalité, le départage se fait par le
score de contenu ci-dessous.

Ces optimisations tournent **en parallèle**, une par réussite, sur les
processus du palier qui vient de se terminer (`_minimize_trial`), et
l'étape « minimisation » est affichée dès leur lancement, avec toutes les
réussites en cours d'optimisation. La grille optimisée de la gagnante est
directement la grille finale (`best_minimized`) : elle n'est pas optimisée
une seconde fois. Seul le cas d'une réussite unique acceptée faute de
mieux, en fin de budget, passe par une optimisation séparée
(`backend/crossword_gen.py`, `generate_grid`).

### Le score de contenu

Toutes les sélections de l'algorithme qui doivent désigner la « meilleure »
grille parmi plusieurs partagent une seule et même formule
(`backend/crossword_gen.py`, `_content_score`) : la **somme des carrés des
longueurs des mots réellement en place**, chaque longueur étant d'abord
plafonnée à **7** lettres (`CONTENT_SCORE_LENGTH_CAP`) avant la mise au
carré, pour qu'un seul mot très long ne domine pas la somme à lui seul. Ce
score favorise quelques mots longs plutôt que beaucoup de mots courts : un
mot de 7 lettres ou plus pèse 49, alors que dix mots de 2 lettres, qui
couvrent pourtant plus de lettres au total, ne pèsent que 40.

- La somme porte toujours sur la **totalité** des mots en place,
  thématiques ou non : un glossaire thématique ou une liste **Mots Défi**
  n'exclut jamais le reste du contenu, il n'ajoute qu'un bonus par-dessus.
- Un mot en place appartenant au glossaire thématique de son propre
  emplacement reçoit **+2** sur sa longueur plafonnée avant la mise au
  carré (`THEME_WORD_SCORE_BONUS`), pour départager en faveur de la
  tentative qui fait le plus ressortir la thématique.
- Un mot de la liste **Mots Défi** reçoit **+4** à la place
  (`CHALLENGE_WORD_SCORE_BONUS`). Les deux bonus ne se cumulent jamais : un
  mot Défi qui est aussi un mot thématique n'est compté qu'une fois, avec
  le bonus Mots Défi.

La même formule départage donc, à l'identique : les réussites parallèles
(ci-dessus, après le tri par nombre de cases noires), la meilleure
tentative *échouée* d'un palier, et le meilleur candidat nettoyé
(chapitre 5).

### Ce que chaque tentative publie en direct

Chaque processus publie, pendant sa propre recherche, chaque nouveau
**record de mots placés** qu'il atteint — pas seulement l'état auquel il
s'arrête. Ces publications remontent au processus parent au fil de l'eau
par un canal dédié (`best_state_queue`), vidé en continu par un processus
léger tournant pendant toute la génération. Le volume reste borné : une
tentative ne peut battre son propre record qu'une fois par mot posé, donc
au plus quelques dizaines de publications par tentative et par palier,
quel que soit le nombre réel d'essais internes.

Deux autres canaux, décrits au chapitre 4 (« Limites de la recherche »),
complètent celui-ci : le compteur de budget consommé, et le battement de
cœur qui republie périodiquement l'état *courant* d'une tentative même sans
nouveau record.

### Le vivier d'affichage n'influence jamais la sélection réelle

**Ces états publiés en direct ne servent qu'à l'affichage, jamais à la
sélection qui décide de la base du palier suivant.** Un état publié tôt
dans une recherche encore peu avancée a mécaniquement très peu de cases
pouvant déjà être jugées injouables (peu de mots posés, donc peu de
croisements pour révéler un conflit) : le comparer au résultat abouti d'une
autre tentative fausserait la sélection. La sélection réelle ne se fie donc
qu'aux résultats finaux de vraies recherches abouties.

La première des grilles montrées à l'écran est toujours, exactement, celle
qui va réellement être nettoyée et conservée pour le palier suivant si elle
est retenue ; les autres peuvent venir du vivier élargi (résultats réels +
états publiés par la file), mais jamais à la place de celle-là, pour que la
comparaison « avant nettoyage / après nettoyage » entre deux aperçus
successifs reste valide.

### Une seule grille par tentative dans le vivier

Une même tentative peut avoir publié plusieurs états successifs en plus de
son résultat final ; sans filtrage, ces instantanés d'une seule tentative
occuperaient à eux seuls plusieurs des places affichées, au détriment des
autres tentatives du palier. Chaque état porte donc l'identité de la
tentative qui l'a produit, et, parmi tous les états d'une même tentative,
seul celui au score le plus élevé est conservé. Cette règle protège aussi
la première grille montrée (celle réellement conservée pour le palier
suivant) : sa tentative d'origine ne peut pas réapparaître plus loin dans
la liste via un de ses propres instantanés antérieurs, un cas possible
puisque cette grille-là est choisie sur un critère légèrement différent
(l'état après nettoyage) de celui qui départage le reste du vivier (l'état
brut).

---

## Chapitre 3 — Phase 1 : poser les cases noires

Chaque tentative part d'une grille de `width` colonnes sur `height` lignes
(15×10 par défaut) — entièrement blanche au premier palier, ou déjà
partiellement noircie et verrouillée si elle hérite d'un palier précédent —
et y ajoute des cases noires **une par une, de façon totalement
indépendante**, sans aucune contrainte de symétrie (`backend/
crossword_gen.py`, `make_pattern`/`_place_black_cells`). Cette absence de
symétrie permet d'atteindre des motifs beaucoup plus clairsemés, donc des
grilles avec beaucoup plus de lettres visibles.

La pose se fait en trois temps :

1. un **pré-remplissage** qui noircit ce qui est de toute façon
   inremplissable (emplacements dont la longueur ou les lettres déjà
   verrouillées ne laissent presque aucun mot candidat) ;
2. une **densification** qui complète jusqu'à l'objectif de pourcentage de
   cases noires réglé dans l'interface ;
3. un **réaménagement** éventuel d'une case noire flottante, pour faire
   exister un emplacement de la bonne longueur pour un mot **Mots Défi**
   qui n'en a pas encore — jamais pour un mot thématique, qui ne prend
   qu'un emplacement déjà offert par le motif.

Chaque case posée doit respecter les règles structurelles ci-dessous, et
n'est jamais posée au hasard sur toute la grille.

### Les règles structurelles

Une case noire n'est acceptée que si elle respecte ces règles :

- une case blanche ne peut jamais se retrouver isolée dans les **deux** sens
  à la fois (entourée de cases noires sur ses 4 côtés) : elle ne ferait
  partie d'aucun mot, dans aucune direction, et ne pourrait donc jamais
  recevoir de lettre — cette règle est absolue, jamais assouplie ;
- la grille blanche doit rester entièrement **connectée** : pas de zone
  blanche isolée du reste par un mur de cases noires ;
- un emplacement encadré par une case noire des deux côtés doit normalement
  faire au moins `STRUCTURAL_MIN_INTERIOR_FREE` cases (**8**), **sauf** si
  l'une de ses deux extrémités touche directement le bord de la grille :
  dans ce cas il reste autorisé quelle que soit sa longueur (y compris 1 ou
  2 cases), et quel qu'en soit le nombre sur la grille entière. Une zone
  d'une seule lettre ne sert jamais de mot à définir ; une zone de deux
  lettres, elle, devient un vrai mot à deviner avec sa propre définition
  (« et », « ou », « no »…).

L'exigence des 8 cases est une préférence esthétique, abaissable d'un cran
à la fois (8, 7, 6… jusqu'à 1) quand elle empêche toute pose — voir
« Éviter l'isolement » ci-dessous. C'est pourquoi `minimize_black_squares`
(chapitre 6), qui ne fait que *retirer* des cases noires, vérifie la grille
avec l'exigence minimale réelle (1 case, c'est-à-dire uniquement la
connexité et l'absence de case orpheline) et non cette exigence
esthétique, qu'il n'est pas de son rôle de faire respecter.

### Choisir où poser une case

Pour éviter que les cases noires se regroupent en petits paquets (ce qui
créerait des murs disgracieux et forcerait beaucoup de mots voisins à avoir
la même longueur), chaque nouvelle case n'est pas tirée au hasard sur toute
la grille : on tire un groupe de **32** positions candidates, et on retient
celle qui se trouve dans la ligne et la colonne les moins déjà chargées en
cases noires.

**Éviter l'isolement.** Parmi ces 32 candidates, on cherche d'abord la
meilleure (au sens du critère ci-dessus) qui **ne touche aucune autre case
noire** et qui respecte l'exigence normale de 8 cases. Si cette exigence ne
laisse plus aucune candidate à la fois isolée et valide, elle est abaissée
d'un cran à la fois (7, puis 6… jusqu'à 1), toujours en cherchant une
candidate isolée à chaque niveau.

**L'adjacence n'est jamais acceptée par la génération de motif**
(`_place_black_cells`), à aucun palier — ni le premier (grille vierge), ni
un palier qui reprend un motif déjà partiellement noirci : si aucune
candidate isolée ne convient à aucun niveau de la cascade, la meilleure
candidate est simplement refusée et retirée du lot, et la case noire n'est
pas posée cette fois-ci. Un palier dont l'objectif de pourcentage ne peut
pas être atteint sans poser une case adjacente finit donc légitimement avec
moins de cases noires que visé : la grille est laissée telle quelle et la
recherche de remplissage est tentée dessus, exactement comme dans tout
autre cas où la cible n'est pas pleinement atteinte.

La même règle s'applique à la phase de pré-remplissage ci-dessous
(`_prefill_unfillable_slots`) : elle aussi cherche uniquement une case
n'en touchant aucune autre, sans jamais accepter l'adjacence en dernier
recours — un emplacement qu'elle ne peut pas réparer ainsi se rabat sur le
retrait d'un mot verrouillé qui le croise, puis, à défaut, est marqué
irréparable pour ce palier.

**Portée de cette interdiction.** Elle ne concerne que la génération de
motif elle-même (`make_pattern`). Les mécanismes de reprise entre paliers
et de résolution des zones impossibles (`_clean_blocked_slots`,
`_build_retry_seed`, `_shorten_impossible_zones`/
`_lengthen_impossible_zones`, le réaménagement d'une case noire flottante)
restent libres de poser ou de déplacer une case noire adjacente à une autre
quand c'est ce que demande la réparation d'une zone déjà impossible.

### Le pré-remplissage

**Avant même** le placement par pourcentage, une phase de pré-remplissage
noircit ce qui ne pourra de toute façon pas être rempli : tant que la grille
comporte un emplacement dont la longueur a **moins de
`PREFILL_MIN_WORD_COUNT` (3) mots candidats** dans le dictionnaire —
typiquement un emplacement trop long, ou d'une longueur trop rare —, on
continue à poser des cases noires jusqu'à ce que ce ne soit plus le cas (ou
jusqu'à ce qu'il ne soit plus possible d'en ajouter, un cas limite accepté,
pas une erreur). Ce seuil de 3 est le même que celui du critère
d'impossibilité appliqué aux emplacements déjà partiellement verrouillés
(ci-dessous) et que celui de la priorité de sélection d'emplacement
(chapitre 4).

#### Ces cases comptent dans l'objectif « Taux noir »

Les cases posées pendant le pré-remplissage comptent pour l'objectif de
pourcentage de cases noires visé (champ **Taux noir** de l'interface,
`black_enrichment_percent`, **17 %** par défaut). Ce pourcentage porte sur
**toute la grille** (lignes × colonnes) et vise un nombre total de cases
noires : les cases noires déjà présentes dans le motif de départ du palier
(reprises d'un nettoyage) comme celles posées par le pré-remplissage y sont
comptées. Si ce total atteint déjà l'objectif, aucune case supplémentaire
n'est ajoutée pour cette raison ; sinon, seule la différence est complétée
(`backend/crossword_gen.py`, `make_pattern`). Une grille qui repart d'un
nettoyage ayant rouvert beaucoup de cases noires est donc ramenée au taux
réglé, et non laissée clairsemée. Le même taux sert de budget par zone au
nettoyage curatif du pré-remplissage (`_prefill_unfillable_slots`).

#### Prise en compte des lettres déjà verrouillées

Le même pré-remplissage tourne aussi, avec un contrôle supplémentaire,
chaque fois qu'un palier reprend un motif déjà partiellement verrouillé
(voir chapitre 5) : en plus de la longueur d'un emplacement, il vérifie,
pour tout emplacement touchant au moins une case déjà verrouillée par une
lettre confirmée, qu'il reste vraiment au moins **3** mots compatibles
**avec ces lettres précises à ces positions précises**
(`PREFILL_LOCKED_MIN_WORD_COUNT`) — pas seulement avec sa longueur en
général. Un emplacement réduit à un unique mot compatible se révèle trop
fragile, le moindre conflit avec une lettre croisée le rendant impossible
sans recours ; 3 offre une marge minimale.

La case noire ajoutée pour corriger un tel emplacement est choisie
**directement parmi les propres cases de cet emplacement** (jamais ailleurs
dans la grille au hasard), sur la case qui touche la ligne/colonne la moins
déjà chargée en cases noires (égalités départagées au hasard). Si aucune
case de l'emplacement ne convient (un vrai blocage, par exemple deux mots
croisés déjà verrouillés qui ne laissent plus aucune case libre pour le
couper), l'emplacement est mis de côté comme « impossible à corriger pour
l'instant » et le pré-remplissage continue avec les autres.

#### Le « nettoyage curatif »

Pour ce même cas — un emplacement rendu insuffisant par des lettres déjà
verrouillées, jamais pour une longueur simplement trop rare, qu'aucun
retrait de mot ne corrigerait — on ne noircit pas cet emplacement
indéfiniment. À sa première détection, on compte combien de cases blanches
il couvre (sa taille d'origine) ; à chaque nouvelle case noire ajoutée pour
le corriger, on compare le cumul de ces cases à son budget propre :
l'objectif de remplissage en noir de la grille entière (le même **Taux
noir**, 17 % par défaut) appliqué à sa taille d'origine, mais **jamais
moins d'1 case noire garantie** (`PREFILL_ZONE_BLACK_BUDGET_FLOOR`). Ce
plancher garantit qu'un emplacement de taille normale (souvent 8 à 15
cases) dispose toujours d'au moins 1 case avant que le pourcentage ne
prenne le relais : appliqué directement à un petit emplacement, le
pourcentage seul pourrait n'autoriser aucune case noire du tout.

Une fois ce budget dépassé (ou si aucune case noire disponible ne
convient), plutôt que de déclarer l'emplacement irréparable, on tente de
**retirer un mot déjà verrouillé qui le croise** — un mot forcément dans
l'autre sens, qui participe aux lettres rendant cet emplacement difficile à
remplir — plutôt que de sur-noircir une seule zone bien au-delà de ce que
l'objectif global prévoit. Le mot retiré est tiré au hasard parmi tous ceux
qui croisent l'emplacement, sans aucun critère de fragilité. L'évaluation
est répétée (une nouvelle case noire, ou un nouveau retrait de mot) jusqu'à
retrouver au moins 3 mots compatibles ; l'emplacement n'est marqué
irréparable que si aucun des deux leviers ne débloque la situation.

### La densité visée

Le pourcentage cible de cases noires de ce placement (`black_ratio`, un
réglage séparé réservé au CLI) est **0 % par défaut, et ne progresse pas
d'un palier à l'autre** : le pré-remplissage combiné au mécanisme de
reprise entre paliers (chapitre 5) suffit à faire progresser la grille sans
la densifier artificiellement palier après palier.

Une petite densification fixe reste appliquée, elle, à chaque palier qui
part d'une grille vierge ou d'une simplification (jamais à un palier de
reprise « telle quelle ») : une fois le pré-remplissage terminé, le
pourcentage **Taux noir** décrit plus haut (`POST_PREFILL_BLACK_FRACTION`,
0,10 par défaut côté moteur) complète le nombre de cases noires jusqu'à ce
pourcentage de la grille entière.

Si le remplissage échoue malgré tout, on ne repart pas forcément de zéro :
voir le chapitre 5. Le nombre maximal de paliers avant d'abandonner est de
**200**.

### Réaménager une case noire flottante pour un mot Défi

Une fois le motif accepté pour ce palier — mais avant même le premier mot
posé — une dernière passe cherche, pour chaque mot **Mots Défi** qui n'a
encore aucun emplacement disponible de sa propre longueur, à façonner le
motif pour qu'un tel emplacement existe
(`_widen_floating_black_cells_for_priority_words`). Les mots du glossaire
**thématique** n'y ont jamais droit : ni élargissement ni raccourcissement
n'est tenté pour eux, ils ne sont placés que sur les emplacements que le
motif offre déjà (`backend/crossword_gen.py`, `_pattern_attempt`,
`_find_priority_word_placement`). Cette passe ne pose jamais elle-même une
lettre : elle se contente de modifier le motif, et c'est le mécanisme
habituel de choix d'emplacement/de mot (chapitre 4) qui s'en saisit
ensuite naturellement, en priorité, comme de n'importe quel autre
emplacement Mots Défi.

**Quand un mot est considéré comme ayant déjà un emplacement.** Un
emplacement vide de la bonne longueur existant ailleurs dans la grille ne
suffit pas à écarter un mot de cette passe (`_has_free_matching_slot`) : il
faut en plus que ses lettres déjà verrouillées concordent avec le mot, et
qu'aucun autre Mot Défi de même longueur, traité plus tôt dans
cette même passe, ne l'ait déjà revendiqué — sans quoi deux mots de même
longueur se verraient tous deux crédités du seul emplacement réellement
libre, ou un mot se verrait crédité d'un emplacement dont les lettres
imposées épellent en réalité autre chose.

**L'élargissement.** Pour chaque mot encore sans emplacement, la passe
examine jusqu'à `WIDEN_BLACK_CELL_WINDOW` cases noires non protégées
(absentes de `permanent_black_cells`), tirées dans un ordre mélangé, et
calcule pour chacune la zone blanche qui résulterait de son déplacement
(`_white_run`, en remontant dans les deux directions depuis la case). Si le
mot tient à ras de l'une ou l'autre extrémité de cette zone élargie — en
respectant toute lettre déjà verrouillée sur ces cases — et que la grille
reste structurellement valide (`is_structurally_valid`, seuil relâché à 1,
le même qu'à la minimisation finale), la case noire est déplacée pour
border le mot de l'autre côté et la case d'origine redevient blanche
(`_try_widen_black_cell`).

**Le repli par raccourcissement.** Une fois que tous les Mots Défi ont eu
leur tentative d'élargissement, une seconde passe
(`_shorten_one_slot_for_word`) s'applique à ceux encore sans emplacement. C'est l'opération miroir : plutôt que
de déplacer une case noire existante pour agrandir une zone, elle cherche
un emplacement déjà vide et **strictement plus long** que le mot, et y case
le mot au début ou à la fin (`_try_shorten_slot`) en posant une case noire
toute neuve juste après sa dernière lettre — aucune case noire existante
n'est touchée, puisque tout l'espace concerné était déjà ouvert. Le nombre
d'emplacements examinés par mot est plafonné à `SHORTEN_SLOT_WINDOW`, soit
10 % de la fenêtre de l'élargissement (`FALLBACK_PHASE_BUDGET_FRACTION`) —
le même budget de 10 % qu'ailleurs dans l'algorithme avant d'abandonner un
mot Défi, repris ici faute de budget de recherche déjà défini
à ce stade, puisque cette passe tourne avant que le remplissage ne
commence.

**Deux garde-fous protègent ces deux manipulations** (déplacer une case
noire existante, ou en poser une nouvelle) contre toute corruption d'un mot
déjà posé ailleurs — indispensable puisque ces mêmes passes servent aussi
en mode Interactif, où la grille peut déjà contenir de vraies lettres en
dehors de la zone remaniée :

1. la case qui absorbe le changement (la « nouvelle » case noire) ne peut
   jamais être une case déjà porteuse d'une vraie lettre ;
2. dans l'axe **perpendiculaire** au mot, ni la case libérée par
   l'élargissement (sa propre lettre du mot en cours étant déjà appliquée)
   ni la case nouvellement noircie ne peuvent faire basculer l'emplacement
   perpendiculaire qui les traverse vers un état sans plus aucun mot réel
   du dictionnaire possible, compte tenu des lettres déjà connues
   (`_perpendicular_slot_stays_valid`/`_slot_has_domain`, une vérification
   de domaine légère, indépendante de `Filler`). Sans elle, libérer une
   case pourrait accoler discrètement une case surnuméraire à un mot
   perpendiculaire déjà posé (le laissant avec une case vide qu'aucune case
   noire ne referme jamais), et noircir une case pourrait au contraire en
   tronquer un.

Grâce à ces deux garde-fous, aucune de ces manipulations ne peut rendre un
emplacement impossible ailleurs dans la grille. C'est un mécanisme du mieux
possible, pas une garantie de résultat : un mot trop long pour la moindre
zone voisine disponible ou pour le moindre emplacement plus long existant,
ou pour lequel aucune manipulation ne reste valide, retombe simplement sur
les chances ordinaires de placement du chapitre 4 — sans jamais rien
corrompre entre-temps. Le coût total est borné par
`WIDEN_PRIORITY_WORDS_LIMIT` (Mots Défi essayés) et
`WIDEN_MAX_SUCCESSFUL` (réaménagements par motif, partagé avec le repli par
raccourcissement).

**Portée.** En génération automatique, ces deux passes ne s'appliquent
qu'au motif neuf de chaque palier (`_pattern_attempt`), jamais à une
reprise « telle quelle » (`_pattern_continue`), dont le motif porte déjà de
vrais mots sur certains emplacements ; les deux garde-fous n'y ont
d'ailleurs aucun effet, la grille étant encore entièrement vierge à ce
stade. Le motif y est remanié **cumulativement** pour toute la liste de
mots d'un coup (jusqu'à `WIDEN_MAX_SUCCESSFUL` réaménagements empilés sur
un même motif), parce que chaque réaménagement est de toute façon conservé :
la recherche qui suit remplit la grille entière en de nombreux placements,
il n'y a pas de « choisir un gagnant, jeter les autres » à respecter.

Le mode Interactif (`interactive_place_word`) réutilise exactement les
mêmes primitives (`_widen_one_floating_black_cell`/
`_shorten_one_slot_for_word`, et à travers elles `_try_widen_black_cell`/
`_try_shorten_slot`/`_perpendicular_slot_stays_valid`) en leur passant les
lettres déjà posées comme verrouillées — c'est là que les deux garde-fous
jouent réellement leur rôle — mais jamais l'orchestration par lot, et
jamais sur un motif partagé : un clic sur **Suivant** ne pose qu'un seul
mot, donc chaque mot encore candidat reçoit sa propre tentative,
entièrement isolée sur sa propre copie du motif (voir « Le mode
Interactif : poser un seul mot », chapitre 4).

Côté interface, un réaménagement de case noire fait partie intégrante de
l'étape posée par **Suivant** : la grille entière renvoyée par le serveur
remplace l'état affiché côté client (pas seulement les cases du mot posé),
de sorte qu'un clic sur **Précédent** annule aussi bien le mot posé que le
réaménagement qui l'a accompagné.

---

## Chapitre 4 — Phase 2 : remplir la grille avec de vrais mots

Une fois le motif de cases noires accepté, chaque suite de cases blanches
d'au moins 2 lettres devient un emplacement à remplir (`extract_slots`).
Le remplissage se fait par **essais successifs avec retour en arrière**
(*backtracking* — `backend/crossword_gen.py`, `Filler`/`_backtrack`, appelés
par `try_fill`) :
le programme choisit un emplacement, y place un mot du dictionnaire qui
respecte les lettres déjà posées par les mots croisés voisins, puis passe à
l'emplacement suivant. Si un emplacement ne peut plus recevoir aucun mot
valide — toutes les lettres déjà imposées rendent le mot introuvable, y
compris quand tous les mots qui correspondraient sont déjà utilisés
ailleurs —, le programme revient en arrière, annule le dernier mot posé et
en essaie un autre.

Deux règles absolues encadrent toute cette phase :

- **un mot n'apparaît qu'une seule fois dans la grille** (`Filler.
  used_words`) ;
- **aucun mot n'est posé s'il laisse un emplacement bloqué parmi ceux qu'il
  croise.** Un candidat est toujours jugé sur l'**état qu'il laisse**, et
  cet état est refusé dans les deux cas :
  - il **rend bloqué** un emplacement croisé qui ne l'était pas juste avant
    lui — c'est le seul des deux qui cède, et seulement en tout dernier
    recours (voir « L'unique dérogation » plus bas) ;
  - il **croise un emplacement bloqué** qui le reste après la pose : refusé
    à tous les stades, sans aucune exception. Un emplacement dont le
    candidat remet au contraire un mot à portée (sa lettre remplaçant par
    exemple une graine) n'est plus bloqué après la pose, donc ce cas ne le
    concerne pas.

Un emplacement écarté (jaune) qui est redevenu sain, lui, se croise
librement : voir « Les emplacements écartés » et « Sécurité des
croisements » plus bas.

Avant de lancer la recherche, trois préparations ont lieu (déductions
certaines, graines statistiques, repérage des emplacements condamnés) ;
la recherche elle-même choisit à chaque pas un emplacement, puis un mot.

### Avant la recherche : déductions certaines, graines, emplacements condamnés

#### Les emplacements à une seule possibilité

Le programme cherche d'abord une certitude, pas une tendance : pour chaque
emplacement encore jouable, si les lettres déjà connues à certaines de ses
cases ne laissent plus qu'**un seul mot du dictionnaire** possible
(éventuellement un seul mot en tout pour cette longueur, sans même aucune
lettre connue), les lettres restantes sont directement figées sur ce mot
(`_force_single_candidate_slots`) — ce n'est plus une graine, un indice
qu'un vrai mot peut contredire, mais un fait acquis, puisqu'aucune autre
possibilité n'existe. Figer un tel emplacement peut, par une case de
croisement, réduire à son tour un voisin à une seule possibilité : le
programme recommence donc jusqu'à ce qu'un passage complet ne déduise plus
rien. Un emplacement déjà connu impossible n'est jamais examiné ici.

#### Les graines

Ensuite, le programme se fait une idée statistique de ce à quoi le reste de
la grille pourrait ressembler : pour chaque emplacement encore incertain,
il tire au hasard 100 mots de la bonne longueur — uniquement parmi les mots
réellement compatibles avec les lettres déjà connues, s'il en existe
(`sample_letter_biases`) — et regarde, case par case, quelle lettre revient
le plus souvent. Une case n'est candidate que si cette lettre
« consensuelle » est apparue plus de `LETTER_BIAS_MIN_COUNT` (10) fois sur
les 100 : un consensus trop faible ne garantit pas qu'il reste assez de
mots compatibles une fois la lettre figée.

Parmi les cases candidates, le programme en pioche **au hasard** un certain
nombre pour en faire des **graines** — des indices qui initient les
premiers placements ou les influencent quand d'autres lettres existent
déjà. Leur nombre va jusqu'à un pourcentage réglable dans l'interface (1 %
par défaut) du nombre de cases blanches **encore sans lettre connue** — pas
du total des cases blanches : une case déjà connue avec certitude, héritée
d'un palier précédent, ne compte pas dans cette base, donc le nombre de
graines diminue naturellement à mesure qu'un palier de reprise confirme la
grille. Jamais plus d'une graine par emplacement (une case qui croise deux
emplacements compte pour les deux).

Une graine n'est qu'un indice, jamais un mot posé : dès qu'un véritable mot
est choisi pour un emplacement croisé, sa vraie lettre prend le pas. Une
case déjà connue avec certitude n'est jamais reproposée comme graine. Une
graine réduit en revanche, comme une vraie lettre croisée, le nombre de
mots candidats compatibles avec son emplacement — la règle de sélection
ci-dessous s'appuyant sur ce nombre, une graine lui donne une vraie
priorité de traitement. Un emplacement déjà impossible ne propose jamais de
graine : le sondage ne tirant que parmi les mots compatibles avec les
lettres connues, un tel emplacement n'a simplement aucun mot à tirer.

Ce relevé est tenu **séparément pour chaque sens** : à chaque case, le
relevé de l'emplacement horizontal et celui de l'emplacement vertical qui
s'y croisent sont conservés chacun de leur côté (`sample_letter_biases`,
`Filler.letter_scores_by_dir`). Leur somme sert au classement des mots
candidats (voir « Choisir quel mot essayer ») ; leur croisement — seules
les lettres présentes dans les deux sens, chacune au plus bas de ses deux
décomptes (`_crossed_letter_counts`) — sert au choix de l'emplacement
(niveau 7 de la cascade), au bouton **Stats** du mode Interactif et aux
lettres grises des aperçus.

Ce relevé de lettres ne reste pas figé sur l'état d'avant la recherche. À
chaque mot effectivement posé, les emplacements que ce mot **croise** sont
rééchantillonnés sur leur domaine courant, qui tient déjà compte de la
lettre qui vient d'être écrite (`Filler._refresh_letter_scores_around`) :
ce sont exactement les emplacements dont les possibilités ont changé. Un
emplacement sans plus aucune lettre valide (domaine vide) n'est pas
rééchantillonné — il n'y a plus rien à y mesurer. Le relevé rafraîchi
remplace celui des cases de l'emplacement concerné plutôt que de s'y
ajouter : l'autre contributeur de ces cases est le mot qu'on vient de
poser, dont les lettres sont désormais fixées et ne disent plus rien
d'utile. Dans le relevé par sens, seul le sens de l'emplacement
rééchantillonné est remplacé, l'autre sens restant tel quel : le croisement
confronte ainsi toujours le dernier relevé de chacun des deux côtés. Le
rafraîchissement est défait en même temps que la pose qu'il
suivait, lors d'un retour en arrière, pour qu'un relevé ne survive jamais à
l'état sur lequel il a été mesuré. Le coût reste borné par construction :
au plus un emplacement croisé par case du mot posé, et la recherche calcule
déjà le domaine de chaque emplacement ouvert à chaque étape.

#### Les emplacements condamnés dès le départ

Un emplacement condamné avant même le premier mot (par exemple une case
noire qui coupe un emplacement déjà partiellement verrouillé d'une façon
qui ne correspond à aucun mot du dictionnaire) est repéré juste avant le
démarrage de la recherche (`Filler.mark_immediately_impossible_slots`) et
mis de côté comme « écarté ». La recherche continue à remplir tout le reste
de la grille au lieu de s'arrêter net. Ce balayage n'est qu'une avance : la
vérification de domaine par nœud ci-dessous trouverait les mêmes
emplacements dès son premier appel.

### Le mécanisme de backtracking, en détail

La recherche est une fonction qui s'appelle **elle-même**, un emplacement à
la fois (`Filler._backtrack`), avec deux issues possibles : réussir en
confirmant que tout le reste de la grille peut être rempli à partir de ce
point, ou échouer, en laissant à l'appel supérieur le soin d'essayer autre
chose.

À chaque appel (un « nœud ») :

1. **Calculer les mots encore possibles pour chaque emplacement ouvert**
   (`Filler._domain`) : le dictionnaire est pré-organisé par longueur/
   position/lettre (`build_index`) pour retrouver instantanément, sans
   jamais le relire en entier, tous les mots d'une longueur donnée
   compatibles avec chacune des lettres déjà connues — qu'elles viennent
   d'un vrai mot croisé posé pendant cette tentative, d'une lettre
   verrouillée d'un palier précédent, ou, en dernier recours, d'une graine
   statistique. Un mot déjà utilisé ailleurs (`used_words`) ne compte
   jamais comme candidat valide.
2. **Un emplacement dont il ne reste aucun candidat** (aucun mot
   compatible, ou tous déjà utilisés ailleurs) est marqué **écarté** et
   **le nœud échoue aussitôt** : une pose antérieure de la recherche l'a
   rendu impossible à remplir, et le retour en arrière va la remettre en
   cause. Font exception les emplacements de `Filler._tolerated_dry`, qui
   sont seulement laissés hors de la liste des domaines du nœud : ceux qui
   étaient déjà vides avant que la recherche ne pose quoi que ce soit
   (relevés par `Filler.solve`, `_dry_open_slots` — aucun retour en arrière
   ne peut les ranimer) et, dans la passe de dernier recours, ceux qu'un mot
   de dernier recours a vidés délibérément. Les emplacements qui croisent
   un emplacement toléré restent sélectionnables, mais aucun de leurs
   candidats ne pourra être retenu tant qu'il reste bloqué : le contrôle
   de croisement ci-dessous refuse toute pose qui croise un emplacement
   bloqué.
3. **Choisir un emplacement**, selon la cascade à 9 niveaux décrite plus
   bas, parmi ceux que l'état du nœud rend sélectionnables (voir « Les
   emplacements écartés » juste après).
4. **Essayer les mots candidats un par un**, dans l'ordre décrit plus bas :
   - **chaque candidat compte immédiatement pour le budget de
     vérifications** (voir « Limites de la recherche »), qu'il mène ou non
     à une descente récursive, et le budget comme un éventuel signal
     d'abandon sont consultés à ce moment-là : si l'un ou l'autre est
     atteint, la fonction s'arrête immédiatement, sans poser ce mot. Ce
     contrôle à chaque candidat (et pas seulement à chaque descente)
     garantit qu'un emplacement dont presque tous les candidats cassent un
     croisement ne peut pas faire défiler des milliers de rejets sans que
     le budget soit jamais reconsulté ;
   - le mot est posé provisoirement et ajouté à `used_words` ;
   - **avant d'aller plus loin**, le programme évalue les emplacements que
     ce mot **croise** (précalculés, `Filler._crossing_slots`) : pour
     chacun d'eux encore ouvert et encore sain avant ce placement, il
     recalcule son domaine (`crossing_broken`). Si l'un se retrouve sans
     plus aucun mot disponible, le mot est retiré sur-le-champ — de la
     grille et de `used_words` — et le programme passe au candidat suivant,
     sans jamais descendre plus loin (voir « Sécurité des croisements »
     pour le détail, les trois familles de candidats et la dérogation) ;
   - si aucun emplacement croisé n'est cassé, le programme choisit
     l'emplacement suivant et s'appelle récursivement ;
   - si cet appel réussit, le succès remonte tel quel, sans rien défaire ;
   - s'il échoue, le mot est retiré (grille et `used_words`) et le candidat
     suivant est essayé.
5. **Si aucun candidat ne mène à un succès**, la fonction échoue à son
   tour : l'appel qui l'a choisie retire *son* propre mot et essaie le
   suivant. Le retour en arrière peut ainsi remonter plusieurs emplacements
   d'un coup, jusqu'à en trouver un qui a encore un candidat non essayé.
6. **Un nœud ne fait qu'un nombre limité de descentes** : au bout de
   `MAX_DESCENTS_PER_NODE` (3) descentes récursives sans succès, il échoue
   aussitôt et rend la main à l'appel supérieur, quel que soit le stade
   atteint (voir les quatre temps d'un nœud plus bas), dernier recours
   compris. Une descente est un candidat qui a passé le contrôle de
   croisement et dans lequel la recherche est descendue ; un candidat
   rejeté sur-le-champ par ce contrôle n'en est pas une, pas plus qu'un
   Mot Défi ou un mot du glossaire thématique : toutes les hypothèses
   issues de ces deux glossaires sont explorées, quel que soit le
   compte. Sans cette
   limite, un nœud n'échouerait qu'après avoir épuisé tout son sous-arbre,
   ce qui n'arrive jamais dans le budget sur un vrai dictionnaire : le
   retour en arrière ne remonterait que de quelques niveaux, et un mot
   difficile posé dans les premières phases resterait en place pour toute
   la tentative. Avec elle, le retour en arrière remonte jusqu'à ces
   premiers mots et peut les remplacer. Une valeur `<= 0` supprime la
   limite : toutes les possibilités du nœud sont alors essayées
   (`backend/crossword_gen.py`, `Filler._backtrack`,
   `MAX_DESCENTS_PER_NODE`). Au début d'une tentative, le plafond est
   plus large : un nœud atteint alors que la recherche a posé moins de
   `EARLY_DESCENTS_WORD_COUNT` (5) mots en plus de ceux de l'état initial
   de la tentative (les mots déjà en place au lancement de
   `Filler.solve`) peut faire jusqu'à `EARLY_MAX_DESCENTS_PER_NODE` (10)
   descentes. Le compte est pris à l'entrée du nœud, sur les mots alors
   en place (`backend/crossword_gen.py`, `Filler._backtrack`,
   `EARLY_MAX_DESCENTS_PER_NODE`).
7. **Le retour en arrière saute directement à la cause (backjumping).**
   Un nœud qui échoue indique quels mots déjà posés sont à l'origine de
   son échec — son **ensemble de conflit** :
   - un emplacement vide : les mots qui le croisent, plus ceux qui occupent
     un mot qu'il aurait pu prendre (`_dry_slot_conflict`) ;
   - un emplacement dont tous les candidats rendraient bloqué un
     emplacement croisé sain : les mots qui croisent cet emplacement et
     ceux qui croisent l'emplacement qu'ils bloqueraient
     (`_assigned_crossers`) ;
   - un nœud qui a épuisé ses possibilités : la réunion des ensembles de
     conflit de toutes les possibilités essayées, sans le mot que ce nœud
     y avait posé.

   Un nœud dont le mot ne figure pas dans l'ensemble de conflit qui lui
   remonte ne peut pas être en cause : essayer ses autres candidats
   rejouerait exactement le même échec. Il retire donc son mot et transmet
   l'échec tel quel à son propre appelant, sans rien essayer d'autre. Le
   retour en arrière traverse ainsi d'un coup tous les niveaux sans
   rapport avec le blocage, jusqu'au mot le plus récent réellement
   impliqué, qui essaie alors son candidat suivant. Sans ce mécanisme, les
   mots posés entre le blocage et sa cause (ailleurs dans la grille) sont
   remis en cause un par un, à raison de `MAX_DESCENTS_PER_NODE`
   possibilités par niveau, et le même blocage est rejoué à chacune.
   Un échec dû au budget ou à un abandon n'indique aucun conflit : il
   revient au retour en arrière ordinaire. Un emplacement déjà vide avant
   toute pose de la recherche ne met en cause aucun mot, donc son échec
   remonte jusqu'à la racine (`backend/crossword_gen.py`,
   `Filler._backtrack`, `Filler._fail`, `_last_conflict`,
   `BACKJUMPING_ENABLED`).

Vérifier les voisins directs avant de redescendre suffit : poser un mot ne
peut jamais affecter le domaine d'un emplacement qui ne partage aucune case
avec lui, donc ce contrôle détecte le problème aussi tôt et aussi sûrement
que si toute la grille avait été revérifiée.

#### Fin de la recherche

La recherche réussit dès que chaque emplacement du motif a reçu un vrai mot
du dictionnaire. Elle échoue si le tout premier appel — celui qui n'a
encore rien posé — épuise ses candidats, ou ses `MAX_DESCENTS_PER_NODE`
descentes, sans jamais réussir plus loin, et cela deux fois : d'abord en
recherche stricte, puis dans la seconde passe qui autorise le dernier
recours (voir « L'unique dérogation »).
Sur une grille trop difficile, c'est l'épuisement du budget de
vérifications qui met fin à la tentative avant l'un ou l'autre de ces deux
dénouements.

**Ce que compte le budget** : non pas le nombre d'appels récursifs, mais le
nombre de **tentatives de poser un mot** — chaque candidat essayé compte
pour une unité, qu'il mène à une descente récursive ou qu'il soit
immédiatement rejeté par le contrôle de croisement. Ce comptage garantit
qu'un emplacement dont presque tous les candidats cassent un croisement
(ce qui peut arriver sans provoquer la moindre récursion) épuise bel et
bien le budget lui aussi.

### Les emplacements écartés

Un **emplacement écarté** (affiché en fond jaune dans les aperçus) est une
pure **déprioritisation**, propre à la tentative en cours : un emplacement
trouvé bloqué récemment pendant cette tentative est mis de côté pour que
la recherche n'y revienne qu'une fois qu'aucun autre emplacement ne peut
recevoir de mot. La liste ne garde que les **3 derniers** emplacements
écartés (`MAX_EXCLUDED_SLOTS`, `_RecentSlots`) : un quatrième en fait
sortir le plus ancien, et un emplacement écarté de nouveau redevient le
plus récent — la liste doit désigner les endroits qui viennent de poser
problème, pas recouvrir la grille. **À chaque pose d'un mot**, les
emplacements écartés qu'il croise sont réévalués : le contrôle de
croisement vient justement de vérifier, pour chacun d'eux, s'il reste
bloqué avec ce mot en place ; celui qui ne l'est plus sort de la liste.
Cette sortie n'est pas annulée si le mot est retiré plus tard par le
retour en arrière : l'emplacement est simplement écarté de nouveau s'il
redevient vide. Il n'est jamais muré et n'est jamais hérité d'un
palier précédent
(`Filler._impossible_this_attempt` ; un nouveau `Filler` démarre avec la
liste vide, et `generate_grid` ne transmet jamais ce diagnostic à
`_pattern_continue`).

Trois sources, toutes internes à la recherche, alimentent cette liste :
le balayage préalable (`mark_immediately_impossible_slots`), la
vérification de domaine par nœud (étape 2 ci-dessus), et l'épuisement des
candidats d'un emplacement — quand tous les mots essayés sur l'emplacement
choisi ont été refusés parce qu'aucun ne pouvait être posé sans créer
d'emplacement impossible, cet emplacement est écarté à son tour. Ce
troisième constat est gratuit : la boucle de candidats vient précisément de
les essayer tous.

**Le constat provoque un retour en arrière, le marquage reste une
possibilité.** Un emplacement constaté impossible à remplir en l'état — par
la vérification de domaine, ou parce que chacun de ses candidats rendrait
bloqué un emplacement croisé encore sain — fait échouer le nœud sur-le-
champ : c'est une pose antérieure qui en est responsable. Un emplacement
dont les candidats sont tous refusés **uniquement** parce qu'ils croiseraient
un emplacement toléré (vide dès le départ) ne fait pas échouer le nœud,
qui passe à l'emplacement suivant : aucun retour en arrière n'y changerait
rien. Dans la passe de dernier recours, ce troisième constat ne fait pas
non plus échouer le nœud, pour qu'il puisse atteindre son temps de dernier
recours. Le marquage « écarté », lui, survit au retour en arrière : un
emplacement écarté a peut-être été constaté impossible dans une
configuration antérieure et être redevenu viable depuis, il reste donc une
possibilité du nœud, simplement retentée après les autres
(`Filler._backtrack`, `blameable_rejection`). L'emplacement garde un domaine réellement non vide, donc
il continue d'être croisé librement par les mots voisins — seule sa
priorité de sélection change, contrairement à un emplacement bloqué
(rouge), qu'on ne croise jamais. **Aucun calcul
d'affichage n'y écrit** : un blocage croisé (voir plus bas) est signalé
pour l'instantané où il est constaté, jamais mémorisé — c'est une propriété
de l'état examiné, pas un fait durable sur l'emplacement, et la pose
suivante peut le dissoudre. Laisser un chemin d'affichage écrire dans cette
liste reviendrait à laisser la publication d'aperçus réécrire
l'ordonnancement de la recherche jusqu'à ce que tout soit écarté et que la
déprioritisation ne veuille plus rien dire.

Chaque nœud se déroule alors en quatre temps :

1. essayer de poser un mot sur les emplacements **non écartés** (et non
   gelés), dans l'ordre de la cascade, jusqu'à ce que l'un accepte un
   candidat ;
2. si aucun mot ne peut être posé ainsi, les emplacements écartés sont
   **libérés** : ils redeviennent ordinaires et la recherche continue,
   toujours sans retour en arrière ;
3. si rien ne peut encore être posé **et** que la recherche est dans sa
   seconde passe — celle qui n'a lieu qu'une fois la recherche stricte
   épuisée depuis la racine, voir « L'unique dérogation » plus bas —, le
   nœud repasse une dernière fois en **acceptant un mot qui rend bloqué un
   emplacement croisé**. Croiser un emplacement déjà bloqué reste, là
   aussi, refusé. Pendant la recherche stricte, ce temps n'existe pas ;
4. si même cela ne pose rien, le nœud échoue et le retour en arrière
   ordinaire reprend.

Les quatre temps partagent le même plafond de descentes du nœud
(`MAX_DESCENTS_PER_NODE`, ou `EARLY_MAX_DESCENTS_PER_NODE` en début de
tentative — voir l'étape 6 plus haut) : un nœud qui l'a atteint échoue sans
passer aux temps suivants.

La libération vaut pour toute la descente qui la suit et se défait
d'elle-même en remontant au-dessus du nœud qui l'a déclenchée (`released`
est un simple paramètre de récursion). Un emplacement écarté reste donc
pleinement réutilisable pour le reste de la tentative et est repris
automatiquement, sans bookkeeping particulier, dès qu'un autre placement
change et que son domaine redevient non vide (`_domain` est recalculé à
chaque nœud) ; il cesse d'être affiché en jaune dès qu'un mot y est
effectivement posé.

**`Filler.excluded_slots` est un mécanisme distinct et n'est pas un
emplacement écarté** : il retire purement et simplement un emplacement de
la grille que la recherche doit résoudre — jamais sélectionné, jamais exigé
par la réussite, jamais compté comme croisement cassé, jamais montré par un
diagnostic. Son seul utilisateur est `_optimize_before_cleanup`
(chapitre 5) ; toute génération ordinaire le laisse vide.

Cette liste n'a aucun effet en mode Interactif (`interactive_place_word`),
qui ne fait tourner aucune recherche récursive et construit directement sa
propre liste d'emplacements sélectionnables.

### Choisir quel emplacement remplir

À chaque emplacement à choisir, le programme applique une règle à plusieurs
**niveaux de priorité** (`Filler._select_target_slot`, appelé par
`_backtrack` — et réutilisé tel quel par `interactive_place_word`) :

1. **optionnel, actuellement désactivé** (`ALTERNATE_DIRECTION_ENABLED`) :
   quand ce réglage est activé, on tire d'abord la **catégorie**
   (horizontal ou vertical), avec une probabilité proportionnelle au nombre
   d'emplacements encore libres dans chacune — ce qui fait naturellement
   alterner les deux directions sans imposer d'ordre strict. Tant qu'il est
   désactivé, ce niveau ne change rien : le groupe de départ est l'ensemble
   des emplacements encore libres, les deux directions confondues ;
2. **Mots Défi**, prioritaire sur **tous** les niveaux suivants, y compris
   le niveau 3 : s'il existe au moins un emplacement où un mot de cette
   liste (non encore posé ailleurs, ni abandonné) tient encore compte tenu
   des lettres déjà connues, le choix se restreint à ces emplacements — et
   y reste à travers tous les niveaux suivants. « Tient » s'évalue de façon
   purement **géométrique** — même longueur, et compatibilité lettre à
   lettre avec les seules lettres réellement connues (un mot croisé posé,
   ou une lettre verrouillée — jamais une graine) — et n'exige **pas** que
   le mot appartienne au dictionnaire (`Filler._challenge_word_fits`) : un
   Mot Défi est pris comme une vérité affirmée par l'utilisateur. Un tel
   mot posé sans être un vrai mot du dictionnaire est ensuite signalé comme
   invalide par les diagnostics habituels (`_invalid_fully_known_indices`),
   exactement comme s'il avait été inséré à la main. Ce niveau est
   volontairement placé avant le niveau 3 : une grille bien avancée a
   presque toujours un emplacement à moins de 3 candidats quelque part, et
   le niveau 3 appliqué d'abord écarterait systématiquement tout
   emplacement compatible Mots Défi. Sans aucun mot dans la liste — le cas
   le plus courant — ce niveau ne change rien ;
3. à l'intérieur du groupe obtenu, et **uniquement pour les emplacements de
   4 lettres et plus** (un emplacement de 2-3 lettres a un vocabulaire
   naturellement restreint, cette priorité n'y apporte rien), on choisit en
   priorité les emplacements avec **moins de `PREFILL_MIN_WORD_COUNT` (3)
   mots candidats** — le même seuil que le pré-remplissage du chapitre 3.
   But : résoudre ces emplacements fragiles par un vrai mot pendant que la
   recherche progresse encore, avant qu'un futur nettoyage ne les juge
   insuffisants et n'y ajoute une case noire. Si aucun emplacement du
   groupe n'est sous ce seuil, ce niveau ne change rien ;
4. parmi les emplacements retenus, s'il en existe au moins un ayant déjà
   **au moins une case déterminée par une vraie lettre** (un mot croisé
   posé pendant cette tentative, ou une lettre verrouillée — jamais une
   graine), le choix se restreint à ceux-là : finir un emplacement déjà
   entamé plutôt que d'en ouvrir un nouveau. Si tous sont entièrement
   vierges, ce niveau ne change rien ;
5. **grille thématique uniquement** — parmi les emplacements retenus (déjà
   éventuellement restreints par Mots Défi au niveau 2, ce qui est
   précisément ce qui donne aux Mots Défi la priorité sur le glossaire
   thématique), s'il en existe au moins un où un mot du glossaire
   thématique non encore posé tient encore, le choix se restreint à
   ceux-là : on commence par les zones thématiquement réalisables. Sur une
   grille bilingue, chaque direction est jaugée contre le glossaire de
   **sa propre** langue. Sans thématique, ce niveau ne change rien ;
6. parmi les emplacements retenus, on calcule pour chacun le carré de la
   distance entre sa **case la plus proche du centre de la grille** et ce
   centre. Chaque case de l'emplacement est considérée individuellement, et
   c'est la plus petite distance au carré qui sert de score — et non la
   distance depuis son point médian : il suffit donc à un emplacement long
   d'atteindre le centre par une seule de ses cases pour obtenir un bon
   score. Le centre est le point `((lignes - 1) / 2, (colonnes - 1) / 2)`.
   Un emplacement ayant une case exactement sur le centre obtient 0, le
   score augmentant à mesure que sa case la plus proche s'en éloigne dans
   n'importe quelle direction — un emplacement franchement excentré sur un
   seul axe est donc davantage pénalisé qu'un emplacement à distance
   équivalente répartie sur les deux axes, ce qui resserre le front de
   remplissage autour du centre plutôt que le long d'un losange plat. Ce
   score ne dépend pas de l'état de remplissage, seulement de la position
   fixe dans la grille. On retient, parmi les emplacements au plus petit
   score, une fenêtre de `SLOT_SELECTION_WINDOW_SIZE` (10) emplacements —
   tous si le groupe en compte lui-même moins de 10 —, mélangés au
   préalable pour qu'aucun ordre positionnel ne départage les ex æquo à la
   coupure (`Filler._select_target_slot`) ;
7. parmi les emplacements de cette **fenêtre géométrique**, on cherche le
   **plus petit nombre de lettres encore possibles sur une case encore
   libre**, et la fenêtre se restreint aux emplacements possédant une case
   à ce compte. Le nombre de
   lettres possibles d'une case est celui du calcul **Stats** du mode
   Interactif, compté séparément dans chaque sens puis croisé : chacun des
   deux emplacements qui se croisent à cette case (l'horizontal et le
   vertical) tient son propre relevé des lettres observées à cette case
   sur son échantillon de vrais mots compatibles avec les lettres déjà
   connues ; seules les lettres présentes dans **les deux** relevés sont
   gardées, chacune avec le plus bas de ses deux décomptes, et c'est le
   nombre de ces lettres communes qui est retenu
   (`Filler._slot_min_letter_options`, `_crossed_letter_counts`, lisant le
   même relevé par sens que `_interactive_letter_stats`). Une case que les
   deux sens ne s'accordent sur aucune lettre compte 0 — la plus contrainte
   possible. Ce 0 n'est qu'un signal d'échantillonnage : la case n'est ni
   écartée de ce niveau ni déclarée impossible pour autant, elle est au
   contraire traitée en premier, et c'est la recherche elle-même (domaines
   réels, `Filler.slot_is_blocked`) qui établit s'il s'agit réellement
   d'une case croisée bloquée ; une case qui n'appartient qu'à un seul emplacement garde le
   relevé de ce seul sens. La mesure est limitée par un **seuil de
   longueur décroissant** : on ne mesure d'abord que les emplacements de
   **7 lettres et plus** (`MOST_CONSTRAINED_START_LENGTH`) ; si aucun
   n'a de case libre mesurable (sélection vide ou épuisée), le seuil
   descend à 6 lettres et plus, puis 5, et ainsi de suite jusqu'à **2**
   (`MOST_CONSTRAINED_MIN_LENGTH`) ; le premier seuil qui retient au
   moins un emplacement mesurable est celui appliqué. Les longs
   emplacements sont ainsi résolus sur leur case la plus serrée avant les
   courts, dont les cases serrées reflètent surtout un vocabulaire
   naturellement restreint. Une case déjà
   déterminée par une vraie lettre n'est pas comptée : sa lettre est fixée,
   elle n'offre plus aucun choix à mesurer — sans cette exclusion tout
   emplacement partiellement rempli rapporterait 1. La case la plus
   contrainte de la grille est celle où le remplissage peut échouer le plus
   tôt : on la résout pendant que la recherche a encore de la marge, plutôt
   que de la laisser à un mot croisé qui la fixerait par accident. Si aucun
   emplacement de la fenêtre n'a de case libre mesurable, même au seuil de
   2 lettres, ce niveau ne change rien (`Filler._select_target_slot`,
   `Filler._slot_min_letter_options`) ;
8. cette fenêtre est **retriée** par nombre de lettres déjà posées (le plus
   en premier — même distinction fait-acquis/graine qu'au niveau 4), puis
   **réduite** à ses `SLOT_SELECTION_REFINE_FRACTION` premiers
   emplacements (1/2), mélangée d'abord pour éviter tout biais positionnel
   à la coupure. Le plancher est de 1 emplacement, jamais 0 : la fenêtre
   issue du niveau 7 peut déjà n'en compter qu'un, et un plancher plus élevé
   annulerait la réduction dans ce cas très courant ;
9. cette fenêtre réduite est enfin retriée par un score statistique — la
   somme des carrés des fréquences mesurées (le même échantillonnage qui
   alimente les graines) de la lettre la plus fréquente à chaque case
   **encore libre** de l'emplacement (une case déjà déterminée n'offre plus
   d'option, donc n'est pas comptée) — le plus haut score en premier :
   l'emplacement dont la zone propose statistiquement le plus d'options de
   remplissage, et donc, pour ses voisins croisants, le plus de lettres
   crédibles avec lesquelles composer. La fenêtre est remélangée au
   préalable ; le premier emplacement devient l'emplacement choisi.

### Choisir quel mot essayer

Les mots candidats de l'emplacement choisi sont d'abord **mélangés**, puis
classés selon à quel point leurs lettres correspondent au consensus
statistique observé sur les cases pas encore déterminées par un croisement
(`_candidate_score`, somme des carrés des scores par case) : un mot qui
colle bien au consensus sur plusieurs cases est essayé avant un mot qui n'y
colle pas du tout, plutôt qu'un tirage entièrement aléatoire.

Le premier mot essayé n'est toutefois pas strictement le mieux classé : le
programme pioche au hasard parmi les `CANDIDATE_SCORE_WINDOW` meilleurs
candidats **restants** à chaque fois (50), une fenêtre qui se décale à
mesure que les mots en sortent. Elle est volontairement bien plus étroite
que le domaine d'un emplacement, qui compte couramment plusieurs milliers
de mots : le classement statistique garde ainsi la main — seuls les
candidats les mieux notés sont réellement atteints en premier, les mots
rares restant loin derrière — tout en laissant assez de jeu pour que deux
tentatives parallèles, ou deux clics successifs sur un même état de
grille, ne convergent pas sur le même mot.

Ces trois étapes — mélange, classement statistique, tirage dans la fenêtre
glissante — forment la **règle unique de tirage d'un mot** de ce moteur
(`Filler.ordered_candidates`). Le mode Interactif étant la version pas à
pas du mode automatique, son bouton **Suivant** tire ses mots exactement de
la même façon, avec la même méthode : chacune de ses trois familles (Mots
Défi, glossaire thématique, dictionnaire général) ordonne les candidats de
chaque emplacement par cet appel, puis retient le premier acceptable. Deux
clics successifs sur un même état de grille ne proposent donc pas
forcément le même mot, exactement comme deux tentatives parallèles du mode
automatique explorent le même motif différemment.

Par-dessus ce classement, deux familles prennent la tête, dans cet ordre :

- **Mots Défi** — tout mot de la liste non encore posé ailleurs, ni
  abandonné pour la tentative en cours, et géométriquement compatible avec
  l'emplacement, passe en tête, devant même les mots thématiques ; il n'a
  pas besoin d'appartenir au dictionnaire. Un emplacement dont le
  dictionnaire ne propose par ailleurs aucun candidat n'est alors pas
  considéré comme une impasse tant qu'un tel mot lui reste compatible.
- **Glossaire thématique** — sur une grille thématique, l'ordre obtenu est
  stabilisé en deux blocs : d'abord les candidats du glossaire, puis les
  autres, pour que l'emplacement tente tous ses mots thématiques avant de
  descendre vers un mot ordinaire. Le backtracking fait le reste : un mot
  hors thématique n'est atteint que si aucun mot thématique n'a mené à une
  solution. Étape sautée si tous — ou aucun — des candidats sont
  thématiques.

### Sécurité des croisements et budget d'abandon

Qu'il s'agisse d'un Mot Défi, d'un mot du glossaire thématique ou d'un mot
ordinaire du dictionnaire, un candidat n'est **jamais laissé en place si,
après lui, un des emplacements qu'il croise est bloqué** (plus aucun mot du
dictionnaire disponible **et** plus aucun Mot Défi encore actif pour ce
croisement) — que ce candidat l'ait rendu bloqué ou qu'il l'ait été avant :
le mot est retiré sur-le-champ, sans descendre plus loin, et le programme
essaie le candidat suivant de la même famille, puis, à défaut, de la
famille suivante. Chercher le même mot sur un **autre** emplacement
se fait naturellement au fil du backtracking, puisque les niveaux 2 (Mots
Défi) et 5 (thématique) de la cascade continuent de privilégier tout
emplacement où il tient encore.

**La référence est prise juste avant le placement, et elle est gratuite.**
La liste des domaines calculée en début de nœud omet déjà tout emplacement
trouvé bloqué (lequel est du même coup marqué écarté) : y figurer signifie
donc exactement « cet emplacement avait encore un vrai candidat avant ce
mot ». Un emplacement qui s'assèche seulement maintenant est le fait du
candidat testé et le fait rejeter ; un emplacement déjà bloqué avant n'est
jamais imputé au candidat suivant. Sans cette référence, un unique voisin
déjà bloqué ferait paraître dangereux tous les candidats de tous les
emplacements voisins. Un emplacement écarté redevenu sain figure dans les
domaines, et se trouve donc pleinement protégé comme n'importe quel autre.

**Croiser un emplacement qui reste bloqué est refusé sans exception.** Le
contrôle distingue donc deux cas, et la référence ci-dessus est ce qui
permet de les séparer : un emplacement croisé qui figurait dans les domaines
du nœud était sain avant la pose, donc c'est ce candidat qui l'a rendu bloqué
(`crossing_broken` — refusé, sauf dans la passe de dernier recours) ; un
emplacement croisé qui n'y figurait pas était déjà bloqué, et le rester après
la pose signifie que ce mot croise un emplacement bloqué
(`crossing_still_impossible` — refusé à tous les stades, celle-là
comprise).
Un emplacement écarté redevenu sain figure dans les domaines : il se croise
donc librement, exactement comme n'importe quel autre emplacement sain, du
moment que la pose ne le rend pas bloqué de nouveau.

#### L'unique dérogation, en tout dernier recours

Le critère « tout a été essayé » est **global** à la tentative, jamais
propre à un nœud. La recherche se déroule en deux passes
(`Filler.solve`) :

1. une **recherche stricte**, depuis la racine, où aucun nœud ne peut
   créer d'emplacement bloqué. Le retour en arrière y remonte jusqu'aux
   premiers mots posés et les remet en cause, dans la limite de
   `MAX_DESCENTS_PER_NODE` descentes par nœud ;
2. **seulement si cette recherche stricte est épuisée depuis la racine**
   — la racine elle-même a échoué, pas simplement le budget de
   vérifications épuisé ni la tentative abandonnée —, elle est rejouée
   depuis la racine avec le dernier recours autorisé
   (`Filler.breaking_permitted`). Dans cette seconde passe, un nœud qui a
   exploré toutes ses possibilités strictes repasse une dernière fois en
   acceptant un mot qui **assèche** un emplacement croisé, plutôt que
   d'échouer et de laisser la grille presque vide.

La première passe ne s'épuise dans le budget que lorsque les possibilités
totales sont peu nombreuses : très petite grille, ou grande grille dont
beaucoup de cases sont déjà verrouillées par les paliers précédents. Sur
une grille plus ouverte, le budget s'épuise pendant la recherche stricte :
la tentative se termine alors avec des emplacements encore vides, jamais
avec un emplacement bloqué créé par la recherche, et l'enrichissement de
dernière chance puis le nettoyage entre paliers prennent le relais (voir
chapitre 5). Mieux vaut une grille bien remplie portant une zone
impossible — que le nettoyage répare au palier suivant — qu'une grille
déclarée échouée très tôt, mais seulement une fois que tout le reste a été
tenté.

Cette passe relâche une seule chose : le contrôle « ne pas rendre bloqué un
emplacement croisé encore sain ». Elle garde deux limites strictes :

- **croiser un emplacement déjà bloqué reste refusé** (`crossing_still_
  impossible`), y compris ici : la dérogation autorise à en créer un, jamais
  à écrire dans un emplacement qui l'est déjà. Le seul endroit où croiser un
  emplacement déjà bloqué est permis est la passe de dernière chance, tout
  à la fin du palier échoué (voir « Optimisation avant nettoyage ») ;
- dans la seconde passe, elle n'est **jamais héritée** par un nœud
  inférieur : chacun doit d'abord épuiser ses propres possibilités
  strictes.

Les candidats gardent leur ordre de priorité habituel dans cette passe :
un candidat sans danger reste toujours préféré et n'est dépassé qu'une fois
son propre sous-arbre épuisé.

#### Le budget d'abandon par mot

Ce qui change d'une famille à l'autre n'est que le budget d'essais ratés
par mot :

- un **Mot Défi** ou un **mot thématique** dispose de son propre budget
  (`Filler._challenge_word_budget`/`_theme_word_budget`, chacun
  `FALLBACK_PHASE_BUDGET_FRACTION` = 10 % du `deadline_checks` de la
  tentative, résolu une fois dans `solve()`) : une fois que l'essayer a
  cassé un croisement autant de fois, ce mot précis est abandonné pour le
  reste de la tentative (`_challenge_abandoned`/`_theme_abandoned`) — il
  cesse d'être proposé comme candidat, et, pour un Mot Défi, cesse aussi
  d'excuser un emplacement croisé sans mot de dictionnaire. Le reste du
  budget se consacre alors au reste de la grille plutôt qu'à un mot
  particulièrement difficile à placer ; ce même mot est retenté depuis zéro
  à la tentative suivante, qui repart avec son propre budget ;
- le **dictionnaire général** n'a pas d'identité de mot à suivre, et n'en a
  pas besoin : il essaie simplement ses candidats restants. Un mot accepté
  au titre de la dérogation ci-dessus n'est jamais décompté d'un budget —
  on n'y a pas renoncé, on l'a posé.

### Qu'est-ce qu'un emplacement « impossible » ?

Trois cas, cumulatifs, font juger un emplacement « impossible » (surlignage
rouge des aperçus, retrait de mot ou de case noire au chapitre 5).

**Premier cas — plus aucun mot possible.** Le jugement ne se fonde que sur
des lettres réellement imposées par un mot croisé confirmé **ou déjà
verrouillées d'un palier précédent** — jamais sur une graine statistique :
une graine reste une supposition non vérifiée, qui ne redevient jamais
pertinente une fois la recherche arrêtée, et ne doit donc pas faire
conclure qu'un emplacement n'a aucune solution. Les lettres verrouillées, à
l'inverse, ne sont jamais ignorées, même si aucun mot croisé ne les a
redécouvertes pendant cette tentative : un emplacement entièrement
verrouillé dont la combinaison ne correspond à aucun mot réel doit être
signalé injouable comme n'importe quel autre — sinon le nettoyage entre
paliers ne voit aucun problème à corriger, et la même combinaison invalide
se reconstruit à l'identique, cycle après cycle (`Filler.locked_letters`,
distinct de `Filler.forced_letters`). Un emplacement dont tous les
candidats sont déjà utilisés ailleurs relève de ce même cas.

**Deuxième cas — le blocage croisé.** Deux emplacements encore ouverts qui
se croisent sur une case sont tous deux jugés impossibles dès lors que
leurs lettres encore atteignables à cette case précise (celles de leurs
candidats respectifs, Mot Défi disponible compris) n'ont **aucune lettre en
commun** : aucune combinaison des deux ne pourra jamais être complétée
ensemble, même si chacun, pris isolément, a un domaine parfaitement non
vide. Un emplacement déjà vide de candidats ne peut jamais être la cause
d'un tel blocage (c'est le premier cas qui le couvre) — seul un emplacement
au domaine sain se retrouve signalé pour cette raison, à cause d'un voisin
tout aussi sain avec lequel aucun accord n'est possible
(`Filler._crossing_deadlock_slots` pendant une recherche,
`_crossing_deadlock_indices` en dehors — pour le mode Interactif). La case
précise du conflit s'affiche dans un rouge plus vif que le reste des deux
emplacements (`deadlock_cells`, toujours un sous-ensemble des cases
impossibles, jamais une catégorie séparée).

Ce deuxième cas n'est **pas** détecté par un balayage lancé à chaque nœud :
ce balayage parcourt le domaine entier de chaque emplacement ouvert, qui
peut compter des dizaines de milliers de mots sur une grille encore peu
remplie, et l'y brancher bloquerait la progression visible de la recherche
précisément sur les grilles où il reste le plus à remplir. Il est calculé à
la cadence, bien plus large, des instantanés qui paient déjà ce coût
(`Filler.excluded_zone_cells(include_deadlock=True)`, appelée par
`_publish_new_best` et par l'instantané de diagnostic final) — un blocage apparu
puis résolu entre deux de ces instantanés n'est donc jamais vu.

Le noircissement d'une case est par ailleurs un mécanisme **distinct** du
nettoyage simple pour ce cas : le bouton **Nettoyer** d'Interactif, comme
le nettoyage automatique entre deux paliers, ne fait jamais que retirer les
mots qui croisent un emplacement impossible — jamais noircir une case pour
un blocage croisé (`_clean_blocked_slots` exclut explicitement un tel
emplacement de son alternative « case noire »). Quand les deux
emplacements du blocage sont encore entièrement vides — le cas le plus
fréquent — il n'y a donc rien à retirer et le nettoyage simple reste sans
effet sur cette paire : elle reste signalée impossible jusqu'à une
modification manuelle, ou jusqu'à ce que la génération automatique la
résolve en remaniant son motif de cases noires (un mécanisme entièrement
différent, mutable par nature).

**Troisième cas — le quota de qualité de contenu**, propre à la génération
automatique : une grille par ailleurs entièrement et validement remplie
peut être refusée par un garde-fou appliqué une fois le remplissage
terminé — trop de noms propres (`MAX_PROPER_NOUNS`) ou trop de mots absents
du dictionnaire de gloses (`MAX_NON_GLOSS_WORDS`), selon la difficulté
(`try_fill`). Les mots à l'origine du refus (tout mot fautif réellement
présent dans la grille, pas seulement l'excédent au-delà du quota) sont
alors eux aussi signalés impossibles — mêmes cases rouges, même entrée pour
le nettoyage entre paliers (`_quota_overflow_slot_indices`) — plutôt que de
laisser la tentative paraître inexplicablement « propre » tout en étant
marquée échouée.

#### Rouge, jaune : deux signaux distincts

`Filler.impossible_zone_cells()` (le rouge) ne traite **jamais** un
emplacement écarté comme impossible sur cette seule base : cette liste
enregistre seulement qu'un emplacement s'est trouvé bloqué au moins une
fois, et le retour en arrière le rend très régulièrement viable à nouveau —
le peindre en rouge confondrait « déprioritisé » et « infaisable ».
`Filler.excluded_zone_cells()` donne son propre signal à une
telle case — fond jaune (`.attempt-preview-grid .cell.white.excluded`), et
jamais soustrait du rouge : une case peut être à la fois écartée et
(fraîchement, réellement) impossible, et c'est l'ordre de la cascade CSS
(`.low-candidates`, puis `.excluded`, puis `.noise`/`.impossible`/
`.deadlock`) qui laisse gagner le signal le plus sévère quand plusieurs
s'appliquent : le jaune passe devant l'orange de l'emplacement pauvre,
mais reste derrière le violet et le rouge. Cet affichage rend exactement la liste des emplacements
écartés, filtrée aux emplacements encore non assignés, si bien que
l'affichage et l'ordonnancement de la recherche ne peuvent jamais diverger.

**Portée d'un emplacement écarté : le cycle de vie d'une tentative.** La
liste est réellement peuplée sur exactement trois publications : les deux
rappels en direct de `try_fill` (`_publish_new_best`/`_publish_live_state`,
ce dernier à coût quasi nul, cette méthode ne faisant que des recherches
d'ensemble) ; la vignette « juste terminée » de la boucle de récolte ; et
`last_examples`, l'entrée d'historique navigable de fin de tentative
(`pattern_attempt_failed`/`pattern_found`), publiée avant que
l'optimisation et le nettoyage ne tournent — cette entrée portant le signal
en plus du seul canal `live_preview`, dont l'affichage peut prendre du
retard en mode rapide. Elle est toujours vide sur tout
ce qui est publié après ce point : l'aperçu de début de cycle de la
tentative suivante (la liste appartient à un `Filler` qui n'existe pas
encore) et l'étape d'optimisation d'une recherche réussie (plus rien
d'écarté à signaler).

### Limites de la recherche

#### Le budget de vérifications

La recherche a un budget maximal, proportionnel à la taille de la grille
par défaut (**largeur × hauteur × 2000** vérifications — 300 000 sur la
grille de référence 15×10 ; `try_fill`). Une « vérification » est **une
tentative de poser un mot** (voir « Fin de la recherche » plus haut) : un
mot immédiatement rejeté parce qu'il casserait un croisement compte autant
qu'un mot qui mène plus loin. Depuis l'interface, le sélecteur **Mode**
(Flash/Turbo/Rapide/Moyen/Ultra ; `backend/app.py`, `BUDGET_MODES`) fixe
directement ce budget par tentative à une valeur choisie (1 000 à
5 000 000), sans rapport avec la taille de la grille.

Puisqu'un mot immédiatement rejeté compte autant qu'un mot productif, le
mode **Flash** (1 000 vérifications) reste le plus fragile des cinq : sur
certaines grilles, ce budget peut s'épuiser avant qu'une recherche par
ailleurs remplissable n'ait eu la chance d'aboutir. C'est un compromis
assumé — un budget qui ne laisse jamais une recherche sans espoir tourner
indéfiniment — et le risque disparaît au budget par défaut comme dans tout
mode plus large.

#### Un budget qui s'étire tant qu'une autre tentative en a besoin

Une tentative qui atteint son propre budget ne s'arrête pas forcément
sur-le-champ : tant qu'au moins une autre tentative du même palier tourne
encore sans avoir atteint le sien, la première continue de chercher
au-delà — l'arrêter laisserait simplement son cœur de processeur inoccupé
jusqu'à ce que la plus lente termine, puisque le palier ne peut de toute
façon pas conclure avant elle (`Filler._deadline_reached_without_
extension`/`_siblings_still_racing`, qui lisent deux tableaux partagés pour
tout le palier : le compteur de vérifications de chaque tentative, et un
octet par tentative disant si elle tourne encore — le compteur seul ne
distingue pas « encore en course » de « arrêtée tôt avec un compteur
figé »).

La vérification se fait au moment même où le budget est dépassé, puis
seulement toutes les 500 vérifications ensuite (le rythme auquel chaque
tentative rafraîchit sa propre case, donc vérifier plus souvent ne verrait
rien de plus frais). Dès qu'aucune autre tentative ne tourne encore sous
son budget, le verdict passe à « stop » et **y reste** pour le reste de la
tentative (`_deadline_extension_denied`) : un « stop » transitoire ne
ferait que rejeter un candidat et laisserait la boucle en poser un autre
juste après, sur un compteur ne tombant plus sur un point de contrôle,
sans jamais dérouler la recherche. Ce mécanisme ne change rien au budget
lui-même : il ne retarde l'arrêt d'une tentative en avance que tant que
cela profite réellement à l'occupation du processeur.

Pour une tentative sans visibilité sur des sœurs (mode Interactif,
minimisation des cases noires, exécution CLI solitaire), c'est la règle
simple et inconditionnelle « budget épuisé, on s'arrête ».

#### Le pourcentage de budget affiché en direct

Pendant une recherche, la ligne de statut affiche le pourcentage de ce
budget déjà consommé, **en moyenne**, par l'ensemble des tentatives
parallèles du palier (`generate_grid`) — libellé en pourcentage de
**générations** (« … — 42 % des générations »), republié toutes les 2
secondes (`BUDGET_PROGRESS_REPORT_INTERVAL_S`). La valeur provient d'un
compteur par tentative (une case dédiée d'un tableau partagé entre les
processus du palier, remise à 0 au début de chaque palier), que chaque
tentative met à jour dès que `CHECKS_PROGRESS_REPORT_INTERVAL` (500)
vérifications se sont écoulées depuis sa dernière mise à jour —
indépendamment de tout nouveau record, si bien qu'une tentative qui avance
et recule sans battre son record fait quand même progresser sa valeur au
rythme réel de sa consommation.

Toutes les actions périodiques de la recherche (cette mise à jour, le
battement de cœur ci-dessous, la prise en compte du bouton Stop, l'arrêt
provoqué par une tentative sœur) se déclenchent sur un **seuil écoulé**,
jamais sur un multiple exact du compteur, et sont évaluées aussi bien à
l'entrée de `_backtrack` qu'après chaque candidat compté dans sa boucle
(`Filler._periodic_checkpoints`, `Filler._checkpoint_due`). Le compteur de
vérifications avance d'une unité par candidat essayé, et la plupart des
candidats sont rejetés sans descendre plus loin : une grille bien avancée
peut enchaîner de longues séries de rejets sans jamais rentrer dans
`_backtrack`, et le compteur y enjambe n'importe quel multiple exact. Ces
actions restent donc régulières quelle que soit la proportion de
candidats rejetés.

Aucun pourcentage n'est plafonné à 100 % : le budget d'une tentative étant
élastique (voir ci-dessus), une tentative qui a dépassé son propre budget
continue de chercher tant qu'une sœur n'a pas atteint le sien, et sa
valeur — comme la moyenne du palier — peut dépasser 100 %.

C'est la **moyenne** de ces cases qui est affichée, jamais la plus élevée :
une seule tentative en difficulté (typiquement celle qui accumule le plus
vite des vérifications, un candidat rejeté comptant autant qu'un candidat
productif) ne peut donc pas, à elle seule, faire croire que tout le palier
a épuisé son budget alors que les autres progressent confortablement en
dessous du leur. Le pourcentage disparaît dès que la phase de remplissage
se termine.

Chaque grille de l'aperçu affiche en plus, sur sa propre ligne d'info, ce
même pourcentage **pour elle seule** — la valeur brute de sa case dédiée,
jamais moyennée. Pour une tentative encore en calcul, elle est relue
dans le tableau partagé toutes les 2 secondes, en même temps que la
moyenne, sans attendre que la tentative publie un nouveau record ou un
battement de cœur (`generate_grid`, `_refresh_computing_budget_percents`).
Pour une tentative arrêtée (réussie ou échouée), elle reste figée à la
valeur réellement atteinte au moment de l'arrêt ; un aperçu de début de
cycle part de 0 %, le tableau venant d'être remis à zéro.

#### La grille de l'aperçu continue de bouger même sans nouveau record

Un second mécanisme republie périodiquement (dès que
`LIVE_STATE_HEARTBEAT_INTERVAL` — 5 000 — vérifications se sont écoulées
depuis la précédente publication, plus rare que le
pourcentage ci-dessus puisqu'il coûte la construction d'une grille
complète) l'état **courant** de chaque tentative, qu'elle batte ou non son
record (`Filler.on_live_state`, marqué `"kind": "heartbeat"`). Sans lui,
une tentative traversant un long plateau sans nouveau record affichait une
grille figée, indiscernable à l'écran d'une recherche réellement bloquée,
alors que le programme continue de poser et retirer de nombreux mots. Un
battement de cœur est volontairement allégé : il ne recalcule pas les
blocages croisés (dont le coût est proportionnel au domaine de chaque
emplacement ouvert) et n'est **jamais** ajouté au vivier qui alimente la
sélection de fin de palier — il peut être moins complet que le dernier
record, et ne doit donc jamais y concourir.

#### Abandon anticipé au-delà de 3 emplacements impossibles (désactivé)

Une tentative peut en principe être abandonnée bien plus tôt : dès que plus
de `UNFILLABLE_ABANDON_SLOT_COUNT` (3) emplacements sont jugés impossibles,
elle serait considérée sans espoir raisonnable et arrêtée, plutôt que de
continuer sur un motif aussi largement compromis (vérifié toutes les 500
étapes, pas en continu). Ce mécanisme est optionnel et **actuellement
désactivé** (`UNFILLABLE_ABANDON_ENABLED`) : une tentative ne s'arrête que
par son budget, une annulation, ou en épuisant réellement son arbre de
recherche.

### Fin de la recherche : fermer les emplacements implicites

Une fois la recherche terminée — succès, budget dépassé, abandon, ou
interruption par une tentative sœur — un dernier passage, bon marché,
referme les emplacements restés formellement non assignés alors qu'il ne
leur reste plus qu'un seul mot du dictionnaire encore disponible (pas déjà
utilisé ailleurs) compte tenu des lettres déjà déterminées par de vrais
mots croisants (`_close_implied_slots`, appelée depuis `try_fill`). Ce mot
est confirmé directement, sans attendre que la recherche ait eu l'occasion
de le sélectionner. Répété jusqu'à un point fixe : refermer un emplacement
peut, par une case de croisement, en déterminer un autre.

Sans cette fermeture, une grille pouvait apparaître entièrement remplie
(chaque case blanche pourvue d'une vraie lettre) et sans aucun emplacement
injouable, tout en étant comptée comme échouée — il suffisait qu'un ou deux
emplacements, déjà implicitement déterminés, n'aient jamais été
explicitement choisis avant la fin de la tentative. Ce pas ne fait jamais
progresser la recherche : aucun mot deviné ou statistique n'y est placé, il
ne confirme que ce qui est déjà, par construction, la seule possibilité
restante.

Deux garde-fous :

- contrairement aux « emplacements à une seule possibilité » (qui agissent
  *avant* la recherche, sur les seules lettres verrouillées), celui-ci
  tient compte des mots déjà placés pendant cette tentative : un mot déjà
  utilisé ailleurs ne peut jamais être confirmé une seconde fois, même s'il
  correspond exactement aux lettres en place ;
- la confirmation n'a pas besoin que **toutes** les cases soient déjà
  connues (il suffit qu'il n'en reste qu'un candidat), donc elle peut
  écrire de vraies lettres — et elle est pour cela soumise à la même règle
  que la recherche : elle est **refusée** si elle laisserait bloqué un
  emplacement croisé encore ouvert. Renoncer à la confirmation ne coûte
  rien : le candidat unique de cet emplacement était de toute façon sa
  seule option, donc la grille n'était complétable dans aucun des deux
  cas — et laisser les deux emplacements ouverts garde le nettoyage du
  chapitre 5 capable d'agir sur de vraies cases vides.

### Affichage : lettres verrouillées vs. graines statistiques

Les cases verrouillées (contenu réellement confirmé, porté d'un palier au
suivant) et les cases forcées (une graine statistique) restent deux
ensembles strictement disjoints dans tout aperçu — jamais une même case
dans les deux. Une case verrouillée reste visible avec sa vraie lettre même
si aucun mot croisé ne l'a redécouverte pendant cette tentative, mais elle
n'est jamais comptée comme graine pour autant : `Filler`/`try_fill`
transmettent les deux notions séparément à `build_partial_letters_grid`,
qui les superpose sur la grille affichée mais ne renvoie que les vraies
graines comme « cases forcées » — une case verrouillée ne s'affiche donc
jamais avec le liseré bleu réservé aux graines.

### Le mode Interactif : poser un seul mot

Le bouton **Suivant** (`interactive_place_word`) pose exactement un mot de
plus. Il réutilise la même cascade à 9 niveaux et la même précédence à
trois familles (Mots Défi, puis glossaire thématique, puis dictionnaire
général), mais sans recherche récursive : une seule décision est prise, et
elle doit être bonne du premier coup, puisqu'il n'y a pas de palier suivant
pour réparer.

**Le choix de l'emplacement évite les emplacements bloqués.** Un mot n'est
jamais posé **sur** un emplacement bloqué tant qu'un autre emplacement peut
en recevoir un : la liste des emplacements bloqués est calculée une fois par
clic, au sens rouge du terme — blocages par case croisée bloquée compris
(`_impossible_indices`) — et ces emplacements sortent du tirage de la
cascade ; ils n'y reviennent que si plus rien d'autre n'est sélectionnable,
pour que **Suivant** ne reste jamais coincé.

**Une vérification de sécurité élargie.** Un candidat est écarté dès qu'il
laisse sans aucun mot disponible un emplacement encore ouvert
(`_word_breaks_open_slot`), et la vérification distingue deux cas :

- **un emplacement qu'il croise, qui était sain avant la pose** : ce
  candidat vient de le rendre bloqué. Refusé, sauf dans la passe de tout
  dernier recours décrite ci-dessous — exactement la distinction que fait
  la génération automatique entre `crossing_broken` et `crossing_still_
  impossible` ;
- **un emplacement qu'il croise, déjà bloqué avant la pose et qui le
  reste** : ce mot croise un emplacement bloqué. Refusé à tous les
  niveaux, sans aucune exception : la dérogation de dernier recours
  autorise à créer un emplacement bloqué, jamais à écrire en travers d'un
  emplacement qui l'est déjà ;
- **un emplacement totalement disjoint** ailleurs dans la grille, dont ce
  mot était le dernier candidat disponible : refusé aussi — un glossaire
  thématique typiquement restreint fait qu'un même mot est souvent la
  dernière option de deux emplacements sans aucune case commune. Mais ce
  cas-là, lui, est toléré dès le niveau intermédiaire, faute de quoi une
  seule zone bloquée ailleurs dans la grille suffirait à bloquer
  **Suivant** pour de bon. Un emplacement disjoint **déjà** bloqué avant
  ce clic n'est jamais imputé au candidat en cours
  (`_open_slot_baseline`, calculé une fois par emplacement candidat puis
  réutilisé pour chacun de ses mots).

La génération automatique, elle, garde délibérément un contrôle limité aux
croisements directs, puisque son nettoyage entre paliers répare de toute
façon ce genre de zone.

**Trois niveaux de tolérance, balayés l'un après l'autre**
(`_placement_accepted`, `PLACEMENT_LEVEL_STRICT`/`_DISJOINT`/`_BREAKING`).
Le mode Interactif ne peut pas exprimer par la récursion les trois étapes
du nœud de `Filler._backtrack` (emplacements non écartés, puis écartés
libérés, puis `allow_breaking`), puisqu'un clic ne prend qu'une seule
décision : il rejoue donc sa recherche à trois familles entière, une fois
par niveau, du plus strict au plus tolérant.

1. **strict** : seuls les candidats qui ne cassent rien ;
2. **disjoint** : en plus, un candidat qui laisse sans mot un emplacement
   totalement disjoint ;
3. **dernier recours** : en plus, un candidat qui rend bloqué un
   emplacement encore sain qu'il croise — la seule dérogation à la règle
   « ne jamais créer d'emplacement impossible », et la même que celle de
   la recherche automatique.

Un niveau entier est épuisé — les trois familles, tous les emplacements
encore ouverts — avant que le suivant soit essayé : une pose plus sûre,
où qu'elle soit dans la grille, l'emporte donc toujours sur une pose plus
dommageable, et les Mots Défi gardent leur priorité sur le glossaire
thématique et sur le dictionnaire général à chaque niveau. Tous les
emplacements écartés au niveau précédent sont du même coup *libérés* au
niveau suivant, comme le fait l'étape 2 du nœud automatique. Le dernier
niveau est ce qui évite qu'une grille encore largement vide soit déclarée
impossible : mieux vaut continuer à la remplir en y créant une zone
impossible — que **Nettoyer**, une correction à la main ou **Finir la
grille** répareront, la zone devant bien exister pour pouvoir être
nettoyée — que de s'arrêter là. Un candidat accepté à un niveau tolérant
ne consomme aucun budget d'abandon, comme un mot accepté sous
`allow_breaking` dans la recherche automatique.

**Une seule notion de blocage, partagée avec la recherche automatique.**
Le mode Interactif est la version pas à pas du mode automatique : il doit se
comporter exactement pareil, le retour en arrière étant fait à la main avec
**Précédent**. La vérification ne raisonne donc pas sur le seul domaine de
chaque emplacement croisé — elle appelle `Filler.slot_is_blocked`, la
définition unique d'« emplacement bloqué » (`DOC_ALGO/FR/Lexicon.md`), celle
que peint déjà le rouge à l'écran : plus aucun mot possible, **ou** case
croisée bloquée. C'est exactement le même appel, avec les mêmes arguments,
que fait `Filler._backtrack` pour ses propres candidats. Un emplacement
rouge à l'écran est donc toujours vu comme tel par le code, et un mot n'est
jamais posé en travers, qu'il ait trouvé le blocage ou qu'il vienne de le
créer.

Le coût de ce contrôle — un parcours du domaine des emplacements
concernés — est tenu par un cache par nœud (`options_cache`,
`Filler._letter_options_cached`), qui rend toujours exactement le même
résultat qu'un recalcul complet :

- les lettres encore possibles sur un emplacement ne changent que si un mot
  tombe sur un emplacement qui le croise ; tout le reste est calculé une
  fois par nœud et réutilisé pour chaque candidat ;
- un emplacement qui croise l'emplacement visé change avec chaque
  candidat, mais seulement par la lettre posée sur leur case commune : il
  est mis en cache par sa **signature de lettres connues**
  (`_known_letters_signature`), soit au plus une entrée par lettre
  distincte à cette case, au lieu d'un recalcul par candidat ;
- chaque entrée garde, par position, le **nombre** de mots disponibles
  portant chaque lettre. Le mot en cours d'essai, désormais utilisé, ne
  retire donc une lettre à un emplacement dont il appartient au domaine
  que s'il en était le dernier porteur — ce que les compteurs disent
  directement, sans reparcourir le domaine ;
- un emplacement encore vierge a pour domaine toute la liste de sa
  longueur : ses compteurs sont calculés une seule fois par longueur
  (`_blank_letter_counts`), les mots déjà posés en étant simplement
  déduits.

Un cache périmé lirait « cet emplacement a encore des options » et
masquerait un blocage réel : chaque entrée mémorise l'ensemble des mots
utilisés pour lequel elle a été calculée, et est recalculée, jamais
réutilisée, s'il ne correspond plus.

**Deux phases, chacune isolée** (`_find_priority_word_placement`, partagée
par la famille Mots Défi et la famille thématique) :

1. toutes les combinaisons (mot, emplacement encore ouvert) géométriquement
   possibles sont d'abord essayées sur le motif de base **intact**, en ne
   considérant que les emplacements déjà existants et déjà viables pour le
   dictionnaire — celles de l'emplacement désigné par la cascade d'abord,
   puis celles de tout autre emplacement ouvert (les emplacements suivant
   leur meilleur mot, et les mots de chacun tirés par la règle unique de
   tirage, `Filler.ordered_candidates` — voir « Choisir quel mot
   essayer »), toute combinaison cassant un emplacement étant écartée
   immédiatement ;
2. seulement ensuite, et pour la seule famille Mots Défi (la famille
   thématique s'arrête à la phase 1), un mot qui ne correspond à **aucun**
   emplacement existant de sa longueur reçoit sa propre tentative de
   remaniement de
   case noire (`_try_reshape_for_word`, élargissement puis
   raccourcissement — voir chapitre 3), **entièrement isolée sur sa propre
   copie du motif de base**, jetée aussitôt si elle n'est pas retenue. Un
   `Filler` jetable est construit sur cette seule copie
   (`_build_interactive_filler`) et la même vérification de sécurité y est
   rejouée : le contrôle porte donc toujours sur l'état réel et final que
   ce candidat précis laisserait derrière lui, jamais sur un état mélangé à
   celui d'un autre candidat. Sans cette isolation, le remaniement d'un mot
   totalement étranger (un déplacement de case noire qui redessine les
   limites d'un emplacement) faussait la vérification d'un autre candidat,
   avant d'être lui-même annulé une fois le mot réellement posé
   déterminé — révélant alors, trop tard, un emplacement en réalité
   impossible.

**Le budget d'abandon**, faute de `deadline_checks` à ce stade, se calcule
sur le nombre total de combinaisons plus tentatives de remaniement
envisagées pour cet appel : chaque Mot Défi est abandonné, pour ce seul
clic, dès qu'il a cassé un emplacement à hauteur de 10 % de ce total. Le
bookkeeping d'abandon (`_register_challenge_word_break`/
`_register_theme_word_break`) est toujours appliqué au `Filler` de la
grille elle-même, jamais à une copie isolée, pour qu'il persiste
correctement d'une phase à l'autre. L'exemption vérifiée par
`_word_breaks_open_slot` (un autre Mot Défi encore actif capable de sauver
un emplacement que ce candidat casserait) est toujours tirée du vivier Mots
Défi courant de la grille, quelle que soit la famille en cours.

**Le repli final** se fait sur le dictionnaire général, en balayant tous les
emplacements encore ouverts dans l'ordre de la cascade — celui qu'elle a
désigné d'abord, puis les autres — et non ce seul emplacement : un
emplacement où aucun candidat n'est acceptable au niveau en cours est
écarté (voir « emplacement écarté » dans `DOC_ALGO/FR/Lexicon.md`) et la
recherche passe au suivant. La grille n'est déclarée impossible qu'une fois
qu'aucun emplacement encore ouvert ne peut recevoir de mot, à aucun des
trois niveaux. Dans chaque passe, un emplacement réputé bloqué (rouge)
n'est essayé qu'après tous les autres. Chaque emplacement exclut d'entrée tout Mot Défi ou
mot thématique encore présent dans son domaine : un tel mot a nécessairement
déjà été essayé, sur tous les emplacements de la grille, par l'une des deux
familles précédentes, et s'y est révélé cassant à chaque fois — sauf si
exclure les deux familles ne laisse absolument rien, seul cas où l'un d'eux
est posé en tout dernier recours plutôt que de laisser **Suivant** bloqué.
Les emplacements écartés pendant ce balayage sont renvoyés au panneau
(`excluded_cells`) et affichés en fond jaune, comme dans les
prévisualisations de la génération automatique. Sur chaque emplacement
balayé, les candidats sont tirés par la même règle unique de tirage que la
recherche automatique (`Filler.ordered_candidates` — mélange, classement
statistique, tirage dans la fenêtre glissante, voir « Choisir quel mot
essayer ») et le premier acceptable est retenu.

Une fois un gagnant désigné, son propre motif (le motif de base intact pour
un choix ordinaire, l'unique copie remaniée pour un choix qui en avait
besoin) est reporté dans la grille réelle et les lettres du mot y sont
écrites — aucune passe d'annulation des remaniements inutilisés n'est
nécessaire, puisque seul celui du gagnant a jamais été appliqué.

### Les autres outils du mode Interactif

Toutes ces fonctions travaillent sur une grille ordinaire, sans aucune des
machineries de tentatives parallèles :

- `interactive_slot_candidates` (**Mots**) — les mots du dictionnaire
  compatibles avec les lettres connues d'un emplacement ; les mots
  thématiques sans plafond, les autres plafonnés à
  `INTERACTIVE_SLOT_CANDIDATES_LIMIT` (300).
- `interactive_crossing_words` (**Croisés**) — les lettres et mots
  possibles à une case, dans les deux directions à la fois.
- `interactive_boundary_candidates` (**Début**/**Fin**) — les mots pouvant
  commencer ou finir un emplacement, de 2 lettres jusqu'à sa longueur
  complète, en respectant les lettres déjà posées. Un candidat plus court
  n'est proposé que si la case frontière juste au-delà peut devenir noire
  (pas déjà lettrée, et structurellement valide à `min_interior_free=1`) ;
  le plafond de 300 s'applique par longueur, pour que les longueurs courtes
  ne saturent pas la liste.
- Ces trois fonctions renvoient chaque mot avec ses **positions
  dangereuses** (`_words_with_unsafe_positions`) : les positions où écrire
  cette lettre rendrait **nouvellement** un emplacement croisé impossible à
  remplir (`_unsafe_letter_positions` compare le domaine du croisement
  avant et après, hors mots déjà utilisés ; un croisement déjà impossible
  n'est jamais reporté, et un Mot Défi encore disponible l'exempte). Ces
  positions s'affichent soulignées en rouge ; le bouton « œil » de chaque
  bloc de résultats masque tous les candidats portant au moins une position
  dangereuse, pour ne comparer que les mots sûrs.
- `_interactive_fill_diagnostics` (**Impossibles**) — les cases rouges et
  orange du panneau, en attrapant aussi un mot inventé par les seuls
  croisements et qui n'existe pas. Un Mot Défi y est considéré comme
  faisant partie du dictionnaire : un emplacement que seul un Mot Défi
  pourrait remplir est compté comme « pauvre » (orange) plutôt
  qu'impossible, et un emplacement épelant exactement un Mot Défi n'est
  jamais signalé invalide.
- `_interactive_letter_stats` (**Stats**) — pour chaque case blanche encore
  vide, la lettre la plus fréquente de son sondage statistique
  (`sample_letter_biases` avec `force_fraction=0.0`, donc rien n'est jamais
  forcé dans la grille), relevés horizontal et vertical croisés
  (`_most_probable_letter` : lettres communes aux deux sens, au plus bas
  des deux décomptes), affichée en gris clair. Une case dont tous les
  emplacements croisés sont déjà impossibles, ou dont les deux sens ne
  partagent aucune lettre, est omise. Le bouton est bistable, activé par
  défaut : tant qu'il l'est, les lettres sont redemandées à chaque
  changement de la grille (`frontend/static/script.js`,
  `scheduleInteractiveStatsRefresh`).
- `interactive_clean_impossible_zones`/`interactive_minimize_black_cells`
  (**Nettoyer** / **Nettoyer (+noires)**) — les équivalents manuels du
  nettoyage automatique, le second essayant en plus de retirer chaque case
  noire.
- **Vérifier** — contrôle que chaque mot posé est bien dans le
  dictionnaire ; comme les précédents, il n'a jamais un Mot Défi
  validement posé pour invalide.

---

## Chapitre 5 — Phase 3 : simplifier une tentative échouée

Cette phase intervient quand un palier échoue (aucune de ses tentatives
parallèles n'a donné une grille complète), pour en récupérer ce qui reste
exploitable avant de continuer. Elle **retire** du contenu (mots, cases
noires) plutôt que d'en ajouter — d'où son nom.

Elle se déroule dans cet ordre :

1. une **optimisation** propre à chaque tentative, avant tout nettoyage ;
2. un **dernier recours** : boucher les cases isolées, qui peut sauver une
   grille de justesse ;
3. le choix entre **reprise « telle quelle »** (garder le motif et n'en
   retirer que ce qui bloque) et **nettoyage complet puis motif neuf** ;
4. dans les cas extrêmes, un retour à la **grille entièrement vierge**.

### Optimisation avant nettoyage

Avant même le nettoyage, chaque tentative distincte du palier passe par une
optimisation dédiée, appliquée séparément à chacune
(`_optimize_before_cleanup`) :

1. tout emplacement **entièrement vide** — aucune case ne porte de lettre,
   ni directement ni via un croisement — est verrouillé, ainsi que la ou
   les cases noires qui le bordent immédiatement ;
2. un remplissage complémentaire est tenté sur tout le reste de la grille
   (les emplacements déjà connus impossibles restent de côté, sans quoi
   leur seule présence ferait échouer tout le remplissage) — un budget
   resté inexploité par la recherche d'origine peut ainsi acheter un
   progrès réel, gratuit ;
3. puis un cycle de retrait de cases noires, exactement comme au
   chapitre 6, mais restreint aux cases noires **non** verrouillées : aucune
   case blanche ou noire verrouillée n'est jamais touchée ;
4. enfin une **passe de dernière chance**, la dernière chose faite avant
   que le nettoyage prenne la main.

**La passe de dernière chance.** À cet instant précis, le palier a échoué
et la grille est sur le point d'être déclarée « échouée » : c'est le seul
moment où un mot peut être posé **en travers d'un emplacement déjà connu
impossible** (voir « emplacement bloqué » dans `DOC_ALGO/FR/Lexicon.md`).
Plus la grille porte de mots quand le nettoyage s'applique, plus il en
survit pour le palier suivant. Trois différences avec l'étape 2, et
chacune est ce qui permet à cette passe d'ajouter quelque chose :

- les emplacements **entièrement vides ne sont plus écartés**, donc la
  recherche essaie réellement de les remplir au lieu de les laisser
  verrouillés ;
- les emplacements **impossibles restent écartés**, et c'est précisément
  ce qui autorise à les croiser : `Filler._backtrack` saute un emplacement
  écarté structurellement dans son contrôle de croisement, donc ni
  `crossing_broken` ni `crossing_still_impossible` ne peut rejeter un
  candidat à cause de lui ;
- le résultat est absorbé **même si le remplissage n'aboutit pas** :
  `try_fill` ne renvoie une grille qu'une fois tous les emplacements requis
  résolus, ce que cette grille-là ne peut justement pas faire, alors que
  son propre diagnostic porte le meilleur état partiel atteint — et c'est
  cet état partiel qui est recherché.

Cette passe ne fait qu'**ajouter** des lettres : rien de ce qui est déjà
posé ne peut être perdu ou contredit, et aucune case noire n'est touchée.
Un mot qu'elle pose peut sceller un emplacement impossible en une suite de
lettres complète ne formant aucun mot réel ; le recalcul décrit plus bas
le repère comme n'importe quel autre, si bien qu'un tel emplacement arrive
au nettoyage signalé impossible plutôt que de passer pour valide.

Contrairement au chapitre 6 (exécuté une seule fois, sur la grille finale
déjà réussie), cette optimisation tourne à *chaque* tentative de *chaque*
palier — un vrai coût sur une grille dense en cases noires. Au-delà de
`PER_CYCLE_OPTIMIZATION_SAMPLE_SIZE` (50) cases noires candidates au
retrait, seul un échantillon aléatoire de 50 est essayé par tour ; dès que
l'une est effectivement retirée, le tour s'arrête et un nouveau tirage de
50, recalculé sur l'état à jour, prend sa place. Sous ce seuil, le
comportement reste exhaustif.

Le résultat de cette optimisation **remplace** la tentative d'origine pour
tout le reste du palier : c'est sur cette grille optimisée, jamais sur
l'état brut, que le nettoyage s'applique. L'interface affiche l'état de
chaque tentative avant cette optimisation puis après, pour que le joueur
puisse comparer.

**Les cases verrouillées de l'aperçu « après optimisation »** ne reprennent
jamais celles d'un état antérieur : la grille entière repart de zéro (aucune
case verrouillée), puis seules les cases des emplacements entièrement vides
et leurs cases noires bordantes sont reverrouillées — la seule définition du
« verrouillé » qui ait un sens pour cette étape. Une case ainsi verrouillée
peut malgré tout finir par porter une lettre réelle sans perdre ce statut :
il signifie « la zone est restée hors de portée du retrait de cases
noires », pas « la case est encore vide ». Ces cases incluent aussi bien des
cases blanches que des cases noires, toutes deux mises en évidence avec le
même liseré orange. Le même principe s'applique à l'aperçu de début du
palier suivant : il repart d'une grille entièrement déverrouillée, puis
reverrouille exactement les cases portant une lettre réellement confirmée à
cet instant — jamais un reliquat d'un palier antérieur.

**Les emplacements impossibles sont recalculés après optimisation.** Les
mots pouvant changer pendant les étapes 2 à 4 ci-dessus, la liste n'est
jamais reprise telle quelle : elle est intégralement recalculée sur l'état
final. Un emplacement reste (ou redevient) impossible s'il n'a toujours
aucun candidat réel une fois ses lettres connues appliquées, ou si ses
cases sont désormais toutes couvertes mais que la combinaison obtenue ne
correspond à aucun mot du dictionnaire (voir « Validation des mots
recomposés par croisement » plus bas) ; dans ce dernier cas le mot n'est pas
conservé, l'emplacement reste vide. À l'inverse, un emplacement dont
l'optimisation a fourni un mot valide n'est plus signalé impossible.

### Dernier recours : boucher les cases isolées

Avant même de choisir entre reprise et nettoyage, un dernier recours est
tenté sur la meilleure tentative échouée de ce palier : si tout ce qui
reste sans lettre n'est rien de plus que des **cases isolées** — des cases
blanches sans lettre dont aucun des 4 voisins directs n'est lui non plus
sans lettre —, chacune est bouchée d'une case noire
(`_plug_isolated_cells`). Une case isolée ne peut, par construction, jamais
faire partie d'un emplacement d'au moins 2 lettres encore ouvert : dès
qu'une case sans lettre a ne serait-ce qu'un voisin également sans lettre,
cela révèle un vrai emplacement encore à remplir, et ce dernier recours n'y
touche alors pas du tout.

Si le résultat reste une grille valide (pas de case blanche orpheline créée
ailleurs, grille blanche toujours connexe) **et** que chaque emplacement du
nouveau motif est entièrement rempli d'un vrai mot du dictionnaire, la
grille est directement déclarée **réussie**, exactement comme un
remplissage abouti. Sinon, rien n'est modifié et le palier suit son cours.

### Reprise « telle quelle »

Parmi les tentatives échouées, on regarde la meilleure — celle qui minimise
le nombre de caractères injouables. S'il lui reste au moins un emplacement
non rempli qui n'est ni signalé impossible, ni un emplacement qui en croise
un (un tel emplacement ne sera de toute façon jamais tenté, donc il ne
compte pas comme espoir de progrès), **et** que moins de
`MAX_CONSECUTIVE_CONTINUE_PALIERS` paliers « telle quelle » consécutifs se
sont déjà enchaînés sans nettoyage, le palier suivant repart, **pour chaque
tentative parallèle non réinitialisée**, du motif rigoureusement identique à
celui d'**origine de sa propre tentative** (`_pattern_continue`, jamais
`make_pattern`) — jamais du seul motif de la « meilleure » tentative
rediffusé à toutes.

#### Chaque tentative repart de sa propre grille, partiellement nettoyée

**Chaque** tentative distincte du palier (pas seulement la meilleure) est
nettoyée individuellement : retrait des mots croisant un emplacement
impossible, puis tri par le score de contenu (chapitre 2), départagé par le
nombre de cases noires. Les moins bonnes sont éliminées, autant qu'il y a de
« grilles nouvelles » configurées (voir plus bas), et jamais plus de
grilles ne sont gardées qu'il n'y a de processus non réinitialisés au
palier suivant — N-1 sur N processus, les tentatives de remplacement
(chapitre 2) pouvant rendre davantage de grilles que de processus ;
chacune des grilles
nettoyées survivantes sert de point de départ à l'un des processus non
réinitialisés du palier suivant (`carry_seed_pool_continue`) — une grille
distincte par processus, jamais celle d'un autre.

#### Raccourcissement préalable des emplacements impossibles

Avant tout retrait de mot classique, et **uniquement sur ce chemin de
reprise** (jamais sur le nettoyage complet, qui régénère de toute façon un
motif neuf), chaque emplacement impossible est d'abord examiné pour un mot
plus court (`_shorten_impossible_zones`) : en tête ou en fin de la zone, de
longueur maximale (longueur de l'emplacement moins un) et minimale de 3
lettres (jamais 2 ni moins), en laissant au moins une case vide de l'autre
côté.

Tous les candidats valables sont d'abord rassemblés — toutes longueurs et
les deux côtés confondus, en excluant tout mot déjà utilisé ailleurs, tout
mot dont aucune lettre ne serait réellement nouvelle (aucun progrès), et
tout candidat dont la case frontière (celle qui sépare le mot de la case
vide restante) est déjà couverte par une lettre confirmée ou ne peut pas
devenir noire sans casser la validité structurelle. Un candidat est ensuite
tiré **au hasard** dans cet ensemble complet : la longueur elle-même est
tirée au hasard, jamais préférée la plus longue. Une case déjà couverte par
une lettre confirmée n'est jamais retenue comme case frontière — la noircir
détruirait le mot croisant qui la fixe.

Les deux morceaux résultants — le mot posé et, s'il y en a un, le reste de
l'autre côté de la case frontière — sont contrôlés dès lors qu'ils sont
entièrement déterminés. Le mot posé l'est toujours par construction ; le
reste, s'il compte au moins deux cases et se trouve déjà entièrement
couvert par des lettres confirmées, doit lui aussi correspondre à un mot
réel, sinon toute la combinaison (longueur, côté) est écartée d'un coup.

Avant de retenir un candidat, on vérifie qu'il ne crée pas un **nouvel**
emplacement croisant impossible — un emplacement qui avait encore au moins
un candidat réel avant ce placement précis et n'en a plus aucun après. Un
emplacement croisant déjà impossible avant n'est jamais compté comme une
nouvelle dégradation (`_new_crossing_impossibility`). Si le candidat tiré
créerait un tel blocage, il est écarté et un autre est tiré, jusqu'à
épuisement. Si aucun ne convient, cet emplacement n'est pas touché du tout.

Le motif, les emplacements croisants et les mots déjà utilisés sont
recalculés à neuf avant l'examen de **chaque** emplacement, pas seulement
une fois par tour : l'examen tient donc toujours compte du mot que
l'emplacement précédent vient de poser. Une fois tous les emplacements
impossibles du tour examinés, la détection est relancée sur le motif mis à
jour — un emplacement raccourci peut redevenir jouable, rester trop
contraint (et être raccourci davantage au tour suivant), et les
vérifications de croisement peuvent avoir révélé de nouveaux blocages
ailleurs. Le cycle s'arrête dès qu'aucun emplacement impossible ne peut
plus être raccourci nulle part.

#### Allongement préalable des emplacements impossibles

Complément exact du raccourcissement (`_lengthen_impossible_zones`/
`_find_longer_word_for_zone`), tenté juste après lui sur ce qu'il n'a pas
résolu, et lui aussi réservé à la reprise « telle quelle ». Là où le
raccourcissement rétrécit la zone en ajoutant une case noire à l'intérieur,
l'allongement l'agrandit en repoussant vers l'extérieur l'une de ses cases
noires bordantes : soit en la déplaçant de quelques cases plus loin, soit en
la supprimant purement et simplement quand l'obstacle naturel suivant (une
autre case noire, ou le bord) suffit déjà à borner la zone allongée.

Une case bordante n'est candidate que si elle est effectivement noire (sinon
la zone touche déjà le bord de ce côté), si elle ne borne pas déjà un mot
différent réellement posé — même double critère que la conservation des
cases noires du nettoyage complet : une case qui borne directement un mot
entièrement connu, ou qui a une lettre connue des deux côtés d'un même
axe —, et s'il y a de la place derrière elle. Le nombre de cases blanches
disponibles détermine combien de longueurs sont essayées : allonger de 1
case, de 2, …, jusqu'à absorber toute la place sans poser aucune nouvelle
case noire.

Comme pour le raccourcissement, tous les candidats valables sont rassemblés
(les deux côtés, toutes les longueurs, tous les mots réels compatibles avec
les lettres connues sur la zone allongée, hors mots déjà utilisés), puis un
candidat est tiré au hasard. Une nouvelle case noire n'est jamais posée sur
une case déjà couverte par une lettre confirmée et doit garder la grille
structurellement valide ; supprimer une case noire sans en reposer ne peut,
à l'inverse, jamais violer cette validité. Le contrôle de nouvelle
impossibilité croisée s'applique aussi, d'une part aux cases qui
appartenaient déjà à un emplacement, d'autre part à la case bordante
elle-même — jusque-là noire, donc absente de l'index des croisements — dont
le parcours perpendiculaire est recalculé directement. Motif, croisements et
cases protégées sont recalculés avant chaque emplacement, et la détection
est relancée après chaque tour.

#### Nettoyage automatique des emplacements bloqués

Sur le chemin de reprise, cette étape ne traite que les emplacements encore
impossibles une fois le raccourcissement et l'allongement épuisés ; sur le
nettoyage complet, elle s'applique directement. Avant de transmettre le
motif au palier suivant, tout mot qui croise directement un emplacement
impossible est retiré — ses cases redeviennent libres pour la recherche du
palier suivant — mais, sauf exception (ci-dessous), **aucune case noire
n'est touchée**. Les emplacements déjà connus impossibles sont eux-mêmes mis
de côté (ignorés plutôt que redemandés), pour laisser la recherche continuer
là où elle s'était arrêtée.

**Validation des mots recomposés par croisement.** Avant ce retrait, tout
emplacement encore sans mot mais dont toutes les cases sont déjà
verrouillées par une lettre confirmée est d'abord recomposé
(`_clean_blocked_slots`) — mais uniquement si la combinaison obtenue
correspond à un vrai mot du dictionnaire ; sinon l'emplacement reste sans
mot plutôt que de porter une combinaison jamais vérifiée. Un emplacement
ainsi laissé de côté n'a pas besoin d'être explicitement signalé impossible
ici : ses lettres restent verrouillées, donc la prochaine recherche
redécouvre d'elle-même que la combinaison ne mène à aucun mot
(`mark_immediately_impossible_slots`).

**Exception : ajout d'une case noire (probabilité 1/10).** Uniquement sur ce
chemin de reprise — jamais sur le nettoyage complet, qui régénère déjà un
motif neuf et peut donc déjà ajouter des cases noires par ce biais
(`BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY`). Avant de retirer un mot
croisant un emplacement impossible, on tire au sort : dans un dixième des
cas, on cherche plutôt une case de l'emplacement lui-même à noircir, en
priorité une case qui ne porte pas déjà une lettre confirmée par un autre
mot (noircir une case confirmée détruirait ce mot-là aussi), mais aussi, en
second recours, une case déjà connue si l'emplacement est entièrement croisé
(le cas le plus fréquent en fin de partie) — la case noire retire alors,
comme effet de bord, le mot croisant qui l'occupait. Au sein de chacun de
ces deux groupes, la case retenue en priorité est celle qui appartient au
plus grand nombre d'**autres** emplacements également impossibles à ce
palier : une seule case noire a ainsi une chance de résoudre plusieurs
emplacements à la fois ; à priorité égale, tirage au hasard sans biais de
position. La case retenue doit garder la grille valide une fois noircie ; si
aucune ne convient, on retombe sur le retrait de mot habituel. Poser une
case noire ne libère aucune contrainte sur l'emplacement (contrairement au
retrait de mot) : elle le fait disparaître sous sa forme actuelle, ses
fragments réels n'étant redécouverts qu'au palier suivant.

Le nouveau motif, une fois cette case ajoutée, peut faire apparaître un
emplacement entièrement couvert par des lettres déjà confirmées ailleurs. Ce
mot n'est transmis comme lettres pré-définies du palier suivant que s'il
correspond réellement à un mot du dictionnaire (`_clean_continue_candidate`,
`_invalid_fully_known_indices`) — sinon l'emplacement reste sans mot.

**Zone strictement sans issue.** Une fois tous les mots croisants retirés,
si l'emplacement n'a *toujours* strictement aucun candidat réel une fois
toute contrainte de croisement levée — typiquement une longueur que le
dictionnaire ne couvre pas du tout —, plus aucun retrait de mot ne pourra
jamais le débloquer : chacune de ses cases restantes est alors directement
noircie (toujours sous réserve de garder la grille valide, case par case),
plutôt que de laisser cette zone resurgir identique à chaque nettoyage
futur.

**Le mot et sa case noire associée forment une unité.** Le raccourcissement
et l'allongement autorisent délibérément un mot posé à croiser un
emplacement **déjà** impossible ailleurs (seule une nouvelle dégradation
rejette un candidat). Un tel mot peut donc, quelques lignes plus loin dans
ce même nettoyage, se retrouver retiré par le retrait des mots croisants. La
case noire posée ou déplacée spécifiquement pour lui est alors annulée dans
le même mouvement — remise blanche (raccourcissement), ou remise noire à son
ancien emplacement et sa nouvelle case remise blanche (allongement) —
plutôt que de rester en place sans plus aucun mot pour la justifier : l'un
ne survit jamais au retrait de l'autre.

#### Fréquence des nettoyages complets

Bornée par `MAX_CONSECUTIVE_CONTINUE_PALIERS` (4) : un nettoyage peut
toujours survenir plus tôt (dès que le motif courant n'a plus aucun espoir
de progrès), mais jamais plus tard que 4 paliers « telle quelle »
consécutifs — ce plafond est systématique, même si la reprise aurait encore,
en théorie, un espoir de progrès.

### Simplification puis motif neuf

Si, au contraire, plus aucun emplacement non rempli n'a de chance d'aboutir
(tous ceux qui restent sont impossibles), on simplifie la tentative en
**deux temps, toujours dans cet ordre** : d'abord on retire les mots qui
croisent directement un emplacement impossible ; ce n'est **qu'ensuite**
qu'on décide quelles cases noires garder — on rouvre (repasse en blanc)
toute case noire qui ne borde plus aucun des mots ayant survécu, et on ne
garde noire qu'une case strictement entre deux lettres confirmées, ou juste
avant/après un mot conservé. Cet ordre compte : décider des cases noires se
fait à partir de ce qui reste *après* le retrait des mots, jamais avant
(`_build_retry_seed`).

Ce premier retrait retire **tous** les mots croisant un emplacement
impossible, d'un coup — jamais un seul à la fois. La même exception que
ci-dessus subsiste : avec une probabilité d'1/10, ce retrait est remplacé
par l'ajout d'une case noire sur l'emplacement impossible lui-même, tentée
une seule fois par emplacement ; ce n'est que si cette alternative n'est pas
tentée ou échoue que le retrait a lieu. Une case noire déjà présente dans le
motif reçu en entrée du palier reste toujours noire, quoi qu'il arrive à son
propre mot.

#### Score et sélection parmi les tentatives nettoyées

Ces deux temps sont appliqués à **toutes** les tentatives échouées et
distinctes du palier, pas seulement à la meilleure. Chacune, une fois
nettoyée, reçoit le score de contenu du chapitre 2 (un mot n'est « en
place » que si toutes ses cases sont confirmées), départagé à score égal par
le **nombre de cases noires** de la candidate — la plus noire l'emporte,
pour laisser plus de marge de manœuvre structurelle au palier suivant sur
une grille très largement verrouillée.

Triées du meilleur score au moins bon, les grilles nettoyées les moins
bonnes sont **éliminées** — autant qu'il y a de « grilles nouvelles »
configurées, et au-delà toutes celles qui dépassent le nombre de processus
non réinitialisés du palier suivant (N-1 sur N processus, moins une par
grille écartée ; les tentatives de remplacement du chapitre 2 peuvent
rendre plus de grilles que de processus), jamais au point de vider la
sélection (il en reste toujours au moins une) (`_seed_pool`). Chacune des survivantes sert alors de point de
départ à l'un des processus non réinitialisés du palier suivant : dans le
cas normal (autant de tentatives échouées distinctes que de processus), le
nombre de survivantes correspond exactement au nombre de places à pourvoir,
chacune recevant sa propre grille de départ.

#### Une tentative repart d'une grille entièrement vierge

Juste après un nettoyage complet, **une seule** des tentatives parallèles du
palier suivant repart d'une grille entièrement vierge
(`FULL_RESET_ATTEMPT_COUNT`) ; les autres reprennent chacune sa propre
grille nettoyée parmi les survivantes. Le nombre de grilles nouvelles ainsi
réservées est précisément le nombre de grilles nettoyées éliminées, pour que
chaque place du palier suivant soit pourvue exactement une fois.

Ceci s'applique aussi à la reprise « telle quelle » — mais, contrairement au
nettoyage complet, à **chaque** palier « telle quelle », pas seulement au
premier d'une série : une tentative réinitialisée repart d'un motif
entièrement neuf via `_pattern_attempt` (jamais `_pattern_continue`,
puisqu'il n'y a alors plus de motif ni de verrouillage antérieur à
reprendre).

Aucune case noire n'est jamais ajoutée par le nettoyage de la reprise
« telle quelle » ni par le nettoyage complet lui-même — seuls les mots et
cases noires déjà présents survivent ou disparaissent selon ce que le
nettoyage retire ; une tentative réinitialisée, elle, peut bien sûr en poser
de nouvelles, comme n'importe quel motif neuf.

#### Grilles qui se répètent : nettoyage profond, puis grille écartée

Chaque grille nettoyée est suivie individuellement d'un **nettoyage
complet** au suivant (une reprise « telle quelle » intercalée ne compte
pas et ne remet rien à zéro). Son état après le nettoyage ordinaire —
motif noir/blanc **et** contenu confirmé, fusionnés en une seule grille
comparable — est comparé à tous les états produits par le nettoyage
complet précédent (`backend/crossword_gen.py`, `generate_grid`,
`carry_cleanup_streaks`).

- **Deuxième fois le même état** (`GRID_REPEAT_DEEP_CLEANUP_STREAK`, 2) :
  la grille est nettoyée **plus en profondeur**. En plus des mots qui
  croisent un emplacement impossible, on retire aussi tous les mots qui
  croisent un mot ainsi retiré — un niveau de plus —, ainsi que tout
  emplacement entièrement verrouillé dont la combinaison ne forme aucun
  mot réel (`_build_retry_seed`/`_clean_blocked_slots`, `deep=True`). Ce
  niveau supplémentaire libère les lettres qui, tenues par les mots
  voisins, imposaient à nouveau exactement le même mot et donc la même
  impasse.
- **Troisième fois le même état malgré ce nettoyage profond**
  (`GRID_REPEAT_DISCARD_STREAK`, 3) : la grille est **écartée**. Elle ne
  fait plus partie des grilles reprises au palier suivant, et sa place
  revient à une tentative supplémentaire repartant d'une grille
  entièrement vierge, en plus de celle réservée à chaque nettoyage complet
  (`carry_discarded_count`). Toutes les autres grilles — celles qui
  progressent encore — sont conservées telles quelles.

L'état comparé est toujours celui du nettoyage **ordinaire** : une grille
passée au nettoyage profond est donc bien reconnue si sa tentative
suivante reconstruit la même impasse. Si toutes les grilles d'un palier
sont écartées en même temps, la recherche repart entièrement d'une grille
vierge — motif, contenu, viviers de grilles candidates et compteur de
série « telle quelle » réinitialisés, exactement l'état du premier palier.

Le suivi se limite au nettoyage complet, jamais à la reprise « telle
quelle » : sur cette branche, un motif stable plusieurs cycles de suite
est normal (une case noire n'y est ajoutée qu'une fois sur dix), et
`MAX_CONSECUTIVE_CONTINUE_PALIERS` la borne déjà.

### Un cas force systématiquement le nettoyage

Sans même regarder la condition de la reprise « telle quelle » : si toutes
les tentatives réellement conclues du palier (hors celles interrompues par
la fin d'une autre) ont été abandonnées tôt pour la même raison (plus de 3
emplacements impossibles, voir chapitre 4) — un signal fort qu'aucune n'a de
raison de croire qu'une reprise aboutirait —, le nettoyage se déclenche
directement, sur la meilleure de ces grilles. Ce déclencheur reste sans
effet tant que ce seuil d'abandon est désactivé, ce qui est le cas
aujourd'hui.

### Interruption anticipée du lot (mécanisme désactivé)

Un mécanisme existe pour arrêter, dès qu'une tentative parallèle est jugée
bloquée (plus de 3 emplacements impossibles — seuil lui-même désactivé),
toutes les autres tentatives du même palier aussitôt, sans attendre leur
propre seuil ou leur propre budget (`_worker_batch_abandoned_event`). Il n'a
de sens que si **toutes les tentatives du palier partagent rigoureusement le
même motif** — un motif partagé jugé bloqué par l'une l'est tout autant pour
les autres — jamais si chacune explore un motif différent, auquel cas la
conclusion de l'une ne dit rien de fiable sur celui d'une autre.

C'est pourquoi il n'est **transmis nulle part** : ni au cas « motif neuf »
(chaque tentative y génère son propre motif indépendant), ni à la reprise
« telle quelle » (chaque tentative y repart de sa propre grille nettoyée, ou
d'un motif entièrement neuf si elle est réinitialisée). En pratique, ce
raccourci n'aurait de toute façon plus grand-chose à apporter : l'arrêt
général une fois la fraction d'interruption atteinte (aujourd'hui 100 %,
chapitre 2) coupe déjà court, quel que soit le motif de chacune.

---

## Chapitre 6 — Optimiser la grille finale

Une fois qu'un palier a réussi (une grille entièrement remplie, un vrai mot
dans chaque emplacement), une dernière passe essaie d'**enlever encore des
cases noires** de cette grille déjà valide, pour la densifier davantage —
moins de cases noires, donc plus de lettres visibles, donc une grille plus
intéressante à résoudre (`minimize_black_squares`).

Le principe : pour chaque case noire encore présente, prise
individuellement, on la retire temporairement et on relance un remplissage
complet à cet endroit (`try_fill`, avec un budget propre à cette phase,
`deadline_checks=6_000` — nettement plus petit que celui de la recherche
principale, puisqu'il ne s'agit que de reconfirmer une grille déjà
quasiment remplissable). Deux issues :

- si la grille reste structurellement valide (mêmes règles qu'au
  chapitre 3, mais avec la variante la plus permissive,
  `min_interior_free=1` : seules comptent encore l'absence de case
  orpheline et la connexité — la préférence esthétique pour des zones d'au
  moins 8 cases ne s'applique qu'à la *pose* des cases noires, jamais à ce
  retrait) **et** qu'un remplissage complet réussit **et** que chacun des
  mots du résultat existe bien dans le dictionnaire, le retrait est
  conservé ;
- sinon (grille invalide, remplissage échoué, ou au moins un mot absent du
  dictionnaire), la case noire est remise en place et on passe à la
  suivante.

L'ordre dans lequel les cases noires sont essayées est mélangé à chaque
passage, pour ne jamais favoriser systématiquement une case parce qu'elle
apparaît plus tôt dans la grille. Toute la grille est repassée en revue en
boucle tant qu'au moins un retrait a réussi au dernier tour complet — un
retrait peut en effet rendre réalisable un autre retrait auparavant
impossible (une case noire qui bloquait un allongement d'emplacement peut
elle-même disparaître une fois une voisine retirée).

Cette étape ne peut donc **jamais dégrader** une grille déjà valide : à tout
moment la dernière solution connue et valide est conservée, et une tentative
de retrait qui échoue n'a d'autre effet que de remettre la case noire en
place. Un mot **Mots Défi** déjà posé bénéficie d'une protection
supplémentaire : ses cases sont verrouillées et pré-remplies dans chaque
essai, et exemptées du contrôle final « chaque mot doit être une vraie
entrée du dictionnaire », pour qu'optimiser une grille ne puisse jamais
remplacer ni rejeter un mot Défi déjà en place. Le bouton **Stop** de
l'interface reste actif pendant cette phase : un point de contrôle
(`cancel_event`) est vérifié entre deux cases noires candidates.

---

## Chapitre 7 — L'aperçu affiché pendant la génération

L'interface affiche, en direct, un aperçu de la recherche pour chaque
palier, dans cet ordre : d'abord le **motif de départ** (celui repris du
palier précédent, avec ses éventuelles cases et lettres verrouillées) ;
puis, dès que les cases noires de ce palier sont posées mais avant que la
recherche de mots ne démarre, le **motif noir/blanc** obtenu ; puis, si le
palier échoue, **toutes les meilleures tentatives échouées distinctes**, sans
plafond, avec leurs lettres réelles et leurs diagnostics complets ; puis,
juste avant le nettoyage, l'état de chacune de ces mêmes tentatives une fois
passée par l'optimisation dédiée (chapitre 5).

Chaque tentative dispose de sa propre vignette, bleue tant qu'elle
calcule, puis figée en or (réussie) ou en orange (échouée) quand elle se
termine. Une fois figée, la vignette ignore les derniers aperçus de cette
même tentative qui arriveraient encore : une tentative envoie son dernier
aperçu juste avant de rendre son résultat, et le résultat peut arriver le
premier (`backend/crossword_gen.py`, `generate_grid`,
`_drain_best_state_queue_continuously`).

### Mise en évidence des cases

- **Contour rouge** : la case appartient à un emplacement au moins
  partiellement fixé par un croisement réellement assigné ou par une lettre
  reportée d'un palier précédent.
- **Fond orange** (emplacement pauvre) : la case appartient à un
  emplacement *partiellement* verrouillé (jamais un emplacement entièrement
  verrouillé, déjà un mot confirmé) dont l'intersection avec les lettres
  verrouillées laisse moins de 3 candidats réels dans le dictionnaire.
- **Fond violet** (case croisée injouable) : aucune lettre ne satisfait à la
  fois l'emplacement horizontal et l'emplacement vertical qui s'y croisent —
  chacun des deux peut très bien avoir des candidats bien réels, mais si
  aucun de leurs mots réellement *jouables* (ni déjà posé ailleurs, ni une
  entrée quasi nulle en fréquence, donc probablement pas un vrai mot) ne
  partage la même lettre à cette case, elle reste injouable telle quelle.
- **Fond rouge** (emplacement bloqué) et **rouge vif** (case croisée
  bloquée) : voir « Qu'est-ce qu'un emplacement impossible ? », chapitre 4.
- **Fond jaune** (emplacement écarté) : voir « Rouge, jaune : deux signaux
  distincts », chapitre 4.
- **Lettre gris clair** (lettre statistique) : dans chaque case encore
  vide, la lettre la plus probable d'après le relevé statistique croisé
  des deux sens (voir « Les graines », chapitre 4), tel qu'il se trouve au
  moment de l'aperçu (`Filler.stat_letters`, `Filler.best_stat_letters_for`
  pour l'état record, figé à l'instant où ce record a été atteint).
  Présente sur les aperçus en direct d'une tentative et sur son étape clef
  (échec ou réussite de la tentative, avant optimisation), jamais sur les
  aperçus de début de cycle ni après le nettoyage. Comme les vraies
  lettres, elle ne s'affiche que lorsque le bouton **Voir** est activé
  (`frontend/static/script.js`, `renderAttemptPreview`).

### Cas particulier : palier « motif neuf »

Pour un palier « motif neuf » qui suit un nettoyage complet — celui qui peut
repartir de plusieurs grilles nettoyées distinctes — le premier aperçu (le
motif de départ) montre lui aussi **une grille par grille nettoyée
survivante**, pas une seule : chaque tentative parallèle non réinitialisée
démarre déjà, à cet instant, sur sa propre grille de départ. Dédupliqué par
motif réel, sans aucun plafond. Sur le tout premier palier d'une génération,
une seule grille suffit — il n'y a rien de plus à montrer.

Un palier de reprise « telle quelle » a lui aussi son propre aperçu par
grille du vivier, exactement le même principe. Seule une tentative
*réinitialisée* n'est volontairement pas prévisualisée séparément ici.

Pour ce même palier « motif neuf », le second aperçu — « cases noires
posées » — est calculé et publié par le processus parent lui-même, avant de
soumettre les tentatives parallèles, en reconstruisant exactement le motif
que le processus réel calculera de son côté (la pose des cases noires étant
une fonction pure de ses paramètres, l'appeler une seconde fois avec la même
graine produit le motif identique, au bit près). Ce calcul se fait une fois
par grille de départ distincte sur le point d'être lancée, dédupliqué par
motif réel, sans plafond. Il ne s'applique jamais à une reprise « telle
quelle », dont l'aperçu coïncide déjà avec le motif de départ du cycle.

Cette reconstruction, purement destinée à l'affichage, ne peut en aucun cas
altérer les lettres réellement verrouillées transmises au palier : la pose
des cases noires reçoit toujours sa propre copie indépendante des lettres
verrouillées, jamais l'objet original partagé par le reste de la
génération.

### Numéro de la grille (lignée) qui a produit chaque aperçu

Au-dessus de chaque grille d'aperçu, un préfixe en gras (« Process N : »)
indique le numéro de sa propre **lignée**, ce qui permet de suivre une
grille précise d'un cycle à l'autre même si son classement change. Ce numéro
n'est **pas** le PID du processus qui l'a calculée (une même lignée peut
être traitée par un processus différent d'un palier à l'autre, le pool ne
garantissant aucune affectation fixe) : il suit la **position** de la grille
dans le vivier de départ de chaque palier, héritée d'un palier au suivant
tant que cette lignée existe.

Chaque grille du premier palier reçoit directement son propre numéro
(1..N). À chaque palier suivant, chaque tentative non réinitialisée hérite
du numéro de l'entrée du vivier dont elle repart ; une tentative
réinitialisée n'a par définition aucun numéro à hériter — si elle survit au
tri par score qui construit le vivier du palier suivant, elle reprend alors
le numéro d'une lignée qui, elle, n'a pas survécu (la moins bonne, éliminée
par ce même tri), jamais un numéro tout neuf tant qu'un numéro existant
s'est libéré. Une grille réinitialisée n'a donc, le temps de son premier
aperçu, encore aucun numéro à afficher.

Les grilles d'un même aperçu s'affichent toujours **dans l'ordre des
lignées** (1 à N), jamais par score : ce dernier continue de décider en
coulisses laquelle est réellement conservée comme base du palier suivant,
mais n'influence pas l'ordre d'affichage — une grille précise reste donc
toujours à la même position relative d'un cycle à l'autre. La grille
effectivement retenue comme la meilleure est signalée par un filet vert
autour d'elle, seul indice visuel de son statut.

Seul le tout premier aperçu du tout premier palier n'a aucun numéro à
afficher : il est reconstruit dans le processus parent, avant qu'une seule
tentative parallèle n'ait été soumise.

---

## Résumé en une phrase

CrossWordFalcon place des cases noires indépendamment les unes des autres en
visant très peu de cases noires au départ, tente plusieurs fois en parallèle
(autant que de processeurs par défaut, réglable) de remplir la grille
obtenue avec un vrai dictionnaire en revenant en arrière dès qu'un
emplacement se bloque — sans jamais poser un mot qui croise un emplacement
bloqué, ni qui en rend un bloqué tant qu'une autre possibilité subsiste ;
si tout échoue, il ne repart pas
forcément de zéro — il reprend telle quelle la meilleure tentative tant
qu'elle garde un espoir de progresser, et ne la simplifie (retirer les mots
bloqués, puis rouvrir les cases noires devenues inutiles) en vue d'un motif
entièrement neuf qu'en dernier recours ; puis, une fois une grille valide
trouvée, il essaie d'en retirer encore le plus de cases noires possible pour
la rendre plus dense — sans jamais revenir sur une grille qui fonctionne
déjà.
