# Comment CrossWordFalcon construit une grille

Ce document explique l'algorithme de génération de grilles de
`backend/crossword_gen.py` : comment les cases noires sont posées, comment
les mots sont choisis et posés, et comment une grille bloquée est récupérée
plutôt que jetée.

Il suit l'**ordre d'exécution** de l'algorithme : le principe général, puis
un chapitre par partie, chacun commençant par une description générale
avant ses particularités et exceptions. Le sens exact des termes
(emplacement bloqué, case croisée bloquée, emplacement écarté, emplacement
pauvre, case croisée injouable) est fixé dans `DOC_ALGO/FR/Lexicon.md`.

L'algorithme existe en deux implémentations identiques : Python
(`backend/crossword_gen.py`, citée ici) et Java (`backend_java/`, paquet
`falcon.gen`, chaque fonction citée y ayant son équivalent du même nom en
camelCase). Seule différence : en Java, les tentatives parallèles d'un
palier sont des fils d'exécution d'un même processus.

---

## Principe général

### Le vocabulaire minimal

- Un **emplacement** est une suite d'au moins **2** cases blanches
  consécutives, sur une ligne (horizontal) ou une colonne (vertical),
  destinée à recevoir un mot à définir. Une case blanche seule entre deux
  cases noires dans un sens n'est qu'une case de passage pour le mot qui la
  traverse dans l'autre sens. Une case appartient le plus souvent à deux
  emplacements qui se croisent.
- Une **tentative** est une construction complète et indépendante : un
  motif de cases noires, puis une recherche de remplissage sur ce motif.
- Un **palier** est un lot de tentatives menées **en parallèle**, une par
  processus. S'il échoue, il transmet au palier suivant ce qu'il a de
  récupérable.

### Les trois phases d'une tentative

1. **Poser les cases noires** (chapitre 3) — construire le motif.
2. **Remplir les cases blanches avec de vrais mots** (chapitre 4) — une
   recherche avec retour en arrière (*backtracking*) : choisir un
   emplacement, y essayer un mot compatible avec les lettres imposées par
   les mots croisés, recommencer sur le suivant, et revenir en arrière dès
   qu'un emplacement n'a plus aucun mot possible.
3. **Simplifier ce qui a échoué** (chapitre 5) — retirer de la tentative
   les mots et les cases noires qui bloquent, pour que le palier suivant
   reparte de ce qui reste exploitable.

### La boucle entre paliers

Une génération enchaîne jusqu'à **200 paliers** (`attempts`). Après chaque
palier échoué :

- **reprise « telle quelle »** — le motif de chaque tentative est conservé
  et seulement débarrassé de ce qui bloque, tant qu'il reste un
  emplacement ayant une chance d'aboutir ;
- **nettoyage complet puis motif neuf** — plus aucun emplacement libre n'a
  de chance d'aboutir : seules les lettres confirmées sont gardées et un
  motif est régénéré ;
- **nettoyage profond, puis grille écartée** — une grille nettoyée qui
  reproduit le même état deux nettoyages de suite est nettoyée plus en
  profondeur ; à la troisième fois, elle seule est remplacée par une
  grille vierge.

Chaque palier hérite donc du contenu réellement confirmé par les
précédents.

### La fin de la recherche

Il faut au moins **2** tentatives réussies sur l'ensemble de la recherche
(`MIN_SUCCESSFUL_ATTEMPTS`). La meilleure est alors retenue, une dernière
passe (chapitre 6) lui retire le plus de cases noires possible, les
définitions sont écrites par le modèle de langage (`backend/clues.py`) et
la grille est enregistrée.

### Ce que le joueur voit pendant ce temps

L'interface affiche en direct un aperçu de la recherche : motif de départ,
motif de cases noires obtenu, meilleures tentatives échouées avec leurs
diagnostics, puis leur état après optimisation (chapitre 7).

---

## Chapitre 1 — Lancer une génération

Une grille se construit soit entièrement par le programme, soit à la main,
mot à mot, avec son assistance. Les deux modes partagent la page d'accueil
et tout le moteur décrit ici.

### Mode tout automatique

1. Configurer la grille (langue, taille, difficulté, taux noir, etc.).
2. Choisir un **Mode** parmi Flash/Turbo/Rapide/Moyen/Ultra/Megatron/GridZilla
   (Ultra, Megatron et GridZilla seulement quand la page est ouverte en local) — ce choix ne
   fixe qu'un budget de recherche par tentative (« Limites de la
   recherche », chapitre 4), pas la qualité du résultat.
3. Facultatif : lister des mots dans le champ **Thématique** pour orienter
   le choix des mots, et/ou dans le champ **Mots Défi
   (personnalisation)** pour forcer des mots précis (jamais montrés dans
   la Bibliothèque).
4. Cliquer sur **Générer la grille**.

Tout s'enchaîne ensuite sans autre action, et la grille terminée est
enregistrée automatiquement dans la **Bibliothèque**.

Si aucune grille n'est trouvée au bout des 200 paliers, un bouton
**Continuer** relance 200 paliers en repartant de l'état exact où la
recherche s'est arrêtée (motif, cases verrouillées, mots confirmés)
(`generate_grid`, `_serialize_resume_state`/`_deserialize_resume_state` ;
`backend/app.py`, `POST /api/generate/continue/{job_id}`). Une fois la
grille terminée, **Recalculer** génère un nouveau jeu de définitions sur
une copie, sans refaire le placement des mots.

### Le lexique d'une grille

Le lexique réunit deux dictionnaires par langue :

- `data/wordlist_<langue>_freq.tsv`, issu du corpus, avec ses fréquences :
  la difficulté n'en garde que les mots les plus fréquents
  (`DIFFICULTY_PRESETS` : 66 % en facile, 80 % en moyen, tout en
  difficile), et le niveau facile en retire aussi les mots sans définition
  et les noms propres probables (`load_wordlist`) ;
- `data/wordlist_<langue>_scrabble.tsv`, le dictionnaire Scrabble, versé
  **en entier** aux niveaux moyen et difficile ; au niveau facile, seuls
  ses mots dont la forme accentuée figure dans la table des formes
  fléchies (`data/inflection/<langue>.jsonl`, comme forme ou comme lemme)
  le sont (`load_inflection_keys`, `_scrabble_entries`). Un mot versé
  absent du premier dictionnaire y est ajouté avec la référence de sa
  ligne du dictionnaire Scrabble ; aucun mot Scrabble ne compte dans les quotas de noms propres
  (`MAX_PROPER_NOUNS`) ni de mots sans définition (`MAX_NON_GLOSS_WORDS`),
  et sa fréquence est relevée au moins à `NOISE_FREQUENCY_THRESHOLD` pour
  qu'il ne soit jamais pris pour du bruit (`merge_scrabble_lexicon`, appelé
  par `load_lexicon`).

La construction de la grille n'utilise que la forme nue des mots (26
lettres sans accent). Le lexique chargé ne garde donc en mémoire, par
longueur, que les mots, leur index (position, lettre), leurs deux
fréquences en flottant 32 bits et, pour chaque mot, la position de sa
ligne dans son fichier (`build_index`). La forme accentuée et les lemmes
ne sont relus sur le disque qu'au moment de préparer les définitions
(`word_forms`). Ce lexique est chargé une seule fois par langue et niveau
de difficulté, puis partagé par toutes les générations et toutes les
sessions Interactif (`load_lexicon`, appelé par `generate_grid` et par
`backend/app.py`, `_load_interactive_index`).

### Mode Interactif (construction manuelle assistée)

Même démarrage, avec le **Mode** « Interactif ». Le guide ci-dessous
s'ouvre aussi dans l'interface par le bouton **?** à gauche de **Mots**.

- Placer ses lettres dans la grille ; Espace ajoute ou supprime une case
  noire.
- **Suivant** pose automatiquement **un** mot de plus, en tenant compte
  d'abord de la liste **Mots Défi**, puis du glossaire thématique (« Le
  mode Interactif : poser un seul mot », chapitre 4). **Précédent** revient
  en arrière.
- Aides : **Dictionnaire**, **Paraphraseur**, et **Mots** (les mots
  compatibles avec l'emplacement sélectionné).
- Deux boutons nettoient la grille sur les zones impossibles, avec ou sans
  retrait des cases noires.
- **Impossibles** identifie les zones où plus aucun mot n'est possible ;
  **Stats** (bistable, activé par défaut) affiche en gris clair, dans
  chaque case vide, la lettre statistiquement la plus probable ;
  **Vérifier** s'assure que tous les mots posés sont dans le dictionnaire
  et ont une définition.
- **Définitions** génère les définitions manquantes ; **Proposer une
  définition** et **Proposer un titre** font plusieurs propositions.
- **Finir la grille** (ou **Finir la zone**, si une zone est sélectionnée)
  confie les cases vides à la génération automatique ; on peut ensuite
  corriger et relancer autant de fois que nécessaire.
- Sauvegarder, puis **Publier** la grille complète : elle rejoint la
  **Bibliothèque**, d'où l'on peut copier un lien ou l'exporter en PDF.

**Finir la grille / Finir la zone** réutilise le pipeline automatique :
chaque case déjà posée devient une contrainte permanente
(`permanent_locked_letters`/`permanent_black_cells`), et, avec une zone
sélectionnée, seules ses cases doivent être résolues pour que la recherche
se déclare réussie (`required_cells`). Un emplacement encore ouvert qui ne
touche aucune case de la zone est retiré de la recherche (`excluded_slots`,
`_outside_zone_slot_indices`, `backend/crossword_gen.py`, `try_fill`) : tout
le budget va aux emplacements de la zone, et ces emplacements hors zone ne
comptent pas non plus comme un espoir de progrès entre deux étapes
(`generate_grid`, `still_has_hope`).

### Grilles bilingues

Une grille peut avoir ses mots horizontaux dans une langue et ses mots
verticaux dans une autre (`generate_grid`, `bilingual_wordlist_path`).
Tout fonctionne alors de la même façon, sauf que chaque emplacement
consulte le dictionnaire de **sa propre direction** — deux dictionnaires
complets et indépendants (`DualIndex`/`DualSet`), qui sont le même objet
sur une grille monolingue.

Chaque mot du résultat porte sa langue (champ `language`), ce qui permet à
`backend/clues.py` d'écrire chaque définition dans la bonne langue, et au
ChatBot (`backend/chatbot.py`) de donner un indice dans la langue du mot.

---

## Chapitre 2 — L'organisation d'un palier

Chaque palier lance **autant de tentatives indépendantes en parallèle que
la machine a de processeurs**, chacune dans son processus
(`PARALLEL_ATTEMPTS` ; variable `CROSSWORDFALCON_PARALLEL_ATTEMPTS` de
`env.sh`) et avec son propre générateur aléatoire, donc son motif et ses
choix de mots. Le palier récolte leurs résultats puis décide : conclure si
le nombre minimal de réussites est atteint, sinon transmettre au palier
suivant ce qu'elles ont produit (chapitre 5).

Au premier palier, **chaque tentative construit sa propre grille depuis
zéro**, motif compris (`make_pattern`). Ensuite, les tentatives partent de
grilles héritées du palier précédent — une grille distincte par tentative.

### Le pré-chauffage du pool de processus

Un pool tout juste créé ne démarre ses processus que paresseusement : un
même processus traiterait plusieurs tentatives pendant que d'autres
restent inactifs, et le même numéro de processus apparaîtrait plusieurs
fois dans un aperçu. Avant le premier palier, `PARALLEL_ATTEMPTS` tâches
factices sont donc soumises d'un coup, chacune bloquée dans une barrière de
synchronisation partagée tant que toutes ne sont pas arrivées, ce qui
force le pool à créer un processus par tâche : la répartition est 1:1 dès
le premier palier.

### Une tentative va toujours jusqu'au bout

Une tentative se poursuit jusqu'à ce qu'il n'y ait plus aucun emplacement
jouable, que son budget de vérifications soit dépassé, ou qu'une tentative
sœur l'interrompe. La décision « reprise telle quelle » / « nettoyage
complet » n'intervient qu'après coup, et aucun processus en cours n'est
jamais tué. Un nettoyage dur précoce (chapitre 5) ne met pas fin à une
tentative : il la fait reprendre sur sa grille nettoyée.

### Interruption anticipée du lot

Une fraction réglable des tentatives, une fois terminées, peut interrompre
celles encore en cours (`generate_grid`,
`PALIER_ATTEMPT_INTERRUPT_FRACTION`, et le point de contrôle de
`Filler._backtrack`). Elle est fixée à **100 %** : le palier attend que
toutes les tentatives se terminent d'elles-mêmes.

### Nombre minimal de réussites et réaffectation des processus

Il faut au moins **2** réussites, cumulées sur toute la recherche : un
palier peut fournir la deuxième réussite d'un palier précédent
(`generate_grid`, `MIN_SUCCESSFUL_ATTEMPTS`).

**Seconde chance.** Une tentative qui échoue alors qu'une tentative
**d'origine** du palier est encore en course n'est pas aussitôt déclarée
échouée (`_second_chance_seed`). Sa grille subit un nettoyage dur des
emplacements bloqués (`_clean_blocked_slots`, sans aucune case noire
ajoutée, déplacée ni rouverte), puis reprend sur le processus qu'elle
vient de libérer, sous le même numéro de grille (`_pattern_continue`). Une
case verrouillée dont le nettoyage efface la lettre est déverrouillée ; les
autres le restent. Elle n'est déclarée échouée que lorsqu'elle aboutit à
un état bloqué (motif et lettres placées) déjà produit dans cette même
chaîne de secondes chances (`_blocked_state_key`). Une tentative que le
bouchage des cases isolées (chapitre 5) complète n'en reçoit pas. C'est le
résultat de la reprise qui figure parmi les résultats du palier, pas la
tentative qu'elle remplace. Une seconde chance se comporte comme une
tentative de remplacement.

**Tentatives de remplacement.** Tant qu'une tentative d'origine est en
course, chaque processus libéré par une réussite, ou par un échec
réellement déclaré, est réaffecté à une nouvelle tentative sur un motif
entièrement neuf — jamais une poursuite de la grille terminée. Ces
tentatives ne prolongent jamais le palier : elles ne comptent pas comme
« en course » pour le budget des autres (`_pattern_attempt`,
`racing=False`), n'ont pas de budget élastique (chacune s'arrête à son
budget, et son processus passe à la suivante), et sont toutes interrompues
dès que chaque tentative d'origine est terminée ou a consommé son budget
(`attempt_done_event`). Interrompue, elle rend sa meilleure grille comme
une tentative échouée.

Un palier rend ainsi plus de grilles qu'il n'a de processus : sur N
processus, seules les **N-1 meilleures** (score de contenu ci-dessous)
sont reprises au palier suivant, plus **1 grille nouvelle** (`_seed_pool`,
plafonné à `PARALLEL_ATTEMPTS - reset_count` ; chapitre 5).

Un palier terminé avec 0 ou 1 réussite ne conclut pas : ses échecs suivent
la reprise du chapitre 5, et la réussite unique reste mémorisée. Si les
200 paliers s'épuisent avec une seule réussite, elle est retenue.

### Choisir la meilleure réussite

La sélection porte sur **toutes** les réussites de la recherche, quel que
soit leur palier — un même palier en fournit souvent plusieurs. Chacune
est d'abord réellement optimisée (minimisation des cases noires,
chapitre 6, sur sa propre copie) ; celle qui a le **moins de cases noires
une fois optimisée** gagne, le score de contenu départageant les égalités.

Ces optimisations tournent **en parallèle**, une par réussite, sur les
processus du palier (`_minimize_trial`), l'étape « minimisation » étant
affichée dès leur lancement. La grille optimisée de la gagnante est la
grille finale (`best_minimized`), sans seconde optimisation ; seule une
réussite unique acceptée en fin de budget passe par une optimisation
séparée (`generate_grid`).

Toutes les réussites optimisées sont aussi rendues comme **grilles au
choix** (`choices`) : chacune avec son score de contenu, triées par score
décroissant (puis moins de cases noires, puis la grille retenue), une par
solution distincte, la retenue marquée « recommandée » (`generate_grid`,
`_final_result`). Pour une génération lancée depuis l'interface (pas par
Populate), le serveur attend alors, avant toute définition, que le joueur
clique sa grille ; sans choix au bout de 10 minutes, la recommandée est
gardée (`backend/app.py`, `_await_grid_choice`).

### Le score de contenu

Toutes les sélections de la « meilleure » grille partagent une formule
(`_content_score`) : la **somme des carrés des longueurs des mots en
place**, chaque longueur étant plafonnée à **7**
(`CONTENT_SCORE_LENGTH_CAP`) pour qu'un mot très long ne domine pas. Elle
favorise quelques mots longs : un mot de 7 lettres pèse 49, dix mots de 2
lettres 40.

- La somme porte sur **tous** les mots en place, thématiques ou non.
- Un mot du glossaire thématique de son emplacement reçoit **+2** sur sa
  longueur plafonnée (`THEME_WORD_SCORE_BONUS`).
- Un mot de la liste **Mots Défi** reçoit **+4** à la place
  (`CHALLENGE_WORD_SCORE_BONUS`) ; les deux bonus ne se cumulent jamais.

Elle départage les réussites (après le tri par cases noires), la meilleure
tentative *échouée* d'un palier, et le meilleur candidat nettoyé
(chapitre 5).

### Ce que chaque tentative publie en direct

Chaque processus publie chaque nouveau **record de mots placés**, par un
canal dédié (`best_state_queue`) vidé en continu par un processus léger.
Le volume reste borné : un record ne peut être battu qu'une fois par mot
posé. Deux autres canaux (chapitre 4, « Limites de la recherche ») le
complètent : le compteur de budget consommé, et le battement de cœur qui
republie l'état *courant*.

### Le vivier d'affichage n'influence jamais la sélection réelle

**Les états publiés en direct ne servent qu'à l'affichage, jamais à la
sélection de la base du palier suivant.** Un état publié tôt a
mécaniquement peu de cases jugées injouables (peu de mots posés, donc peu
de croisements pour révéler un conflit) ; le comparer au
résultat abouti d'une autre tentative fausserait la sélection, qui ne se
fie donc qu'aux résultats finaux.

La première grille montrée est toujours celle qui sera réellement nettoyée
et conservée si elle est retenue ; les autres peuvent venir du vivier
élargi (résultats réels + états publiés), pour que la comparaison « avant
/ après nettoyage » entre deux aperçus reste valide.

### Une seule grille par tentative dans le vivier

Chaque état porte l'identité de sa tentative, et seul son état au score le
plus élevé est conservé : les instantanés d'une tentative n'occupent pas
plusieurs places affichées. Cela protège aussi la première grille
montrée, choisie sur l'état après nettoyage alors que le reste du vivier
l'est sur l'état brut : sa tentative ne réapparaît pas plus loin.

---

## Chapitre 3 — Phase 1 : poser les cases noires

Chaque tentative part d'une grille de `width` × `height` cases (15×10 par
défaut) — blanche au premier palier, ou déjà partiellement noircie et
verrouillée si elle hérite d'un palier précédent — et y ajoute des cases
noires **une par une, sans aucune contrainte de symétrie** (`make_pattern`/
`_place_black_cells`), ce qui permet des motifs bien plus clairsemés.

La pose se fait en deux temps : un **pré-remplissage** qui noircit ce qui
est de toute façon inremplissable, puis une **densification** jusqu'au
pourcentage de cases noires réglé dans l'interface.

Le motif n'est plus retouché avant la recherche. Les cases noires
flottantes ne sont déplacées ou ajoutées que pendant la recherche, par le
nœud qui pose un **Mot Défi** — ou un mot thématique tant que moins de
**5** mots du glossaire sont posés — et retrouvent leur état d'origine dès
que ce mot est refusé (« Réaménager une case noire flottante » ci-dessous
et chapitre 4).

### Les règles structurelles

Une case noire n'est acceptée que si :

- aucune case blanche ne se retrouve isolée dans les **deux** sens à la
  fois (noire sur ses 4 côtés) — règle absolue, jamais assouplie ;
- la grille blanche reste entièrement **connectée** ;
- un emplacement encadré par deux cases noires fait au moins
  `STRUCTURAL_MIN_INTERIOR_FREE` cases (**6**), **sauf** si l'une de ses
  extrémités touche le bord de la grille : il est alors autorisé quelle
  que soit sa longueur (y compris 1 ou 2 cases), et quel qu'en soit le
  nombre sur la grille. Une zone d'une lettre ne sert jamais de mot ; une
  zone de deux lettres devient un vrai mot à définir (« et », « ou »,
  « no »…).

L'exigence des 6 cases est une préférence esthétique, abaissable d'un cran
à la fois (6, 5, 4, 3, 2, 1) quand elle empêche toute pose. C'est pourquoi
`minimize_black_squares` (chapitre 6), qui ne fait que retirer des cases
noires, vérifie la grille avec l'exigence minimale (1 case : connexité et
absence de case orpheline).

### Choisir où poser une case

Le tirage travaille **emplacement par emplacement**. Les emplacements
sont toutes les suites maximales de cases non noires, horizontales et
verticales (cases seules comprises) ; ils sont calculés une fois, puis
tenus à jour à chaque pose : la nouvelle case noire coupe l'emplacement
horizontal et l'emplacement vertical qui la contiennent chacun en deux
morceaux, ou en un seul quand elle est en bout d'emplacement
(`backend/crossword_gen.py`, `_white_runs`, `_split_runs`).

Chaque tirage classe les emplacements du plus long au plus court
(égalités au hasard) et retient les `BLACK_DRAW_WINDOW_PERCENT`
(**5 %**) les plus longs (au moins un). Parmi les cases candidates de ces
emplacements, seules celles qui respectent les **contraintes fortes**
sont conservées. Pour chacune, on retient `BLACK_DISTANCE_NEIGHBORS`
(**7**) cases noires : d'abord les cases **alignées**, la plus proche dans
chacune des quatre directions (gauche, droite, haut, bas — bords compris,
donc toujours quatre), puis les **3** cases non alignées les plus proches.
Son **score** est la **moyenne des racines carrées** des distances
(euclidiennes) à ces 7 cases.
La distance à une case noire située sur la **même ligne ou la même
colonne** compte pour `BLACK_ALIGNED_DISTANCE_FACTOR` (**10**) fois la
distance réelle, ce facteur étant appliqué avant la racine
(`_black_distance_sq`, `_record_black`, `_nearest_black_distances_sq`,
`_black_spread_score`). Les **bords** de la grille comptent comme des
cases noires : un anneau de cases noires virtuelles entoure la grille,
juste à l'extérieur. On classe les cases du meilleur score au moins bon
(à égalité, l'ordre de leur mélange initial) et une case est tirée **au
hasard** parmi les **5 %** de meilleur score (au moins une). Si les emplacements retenus ne contiennent
aucune case valide, le pourcentage est **augmenté de 5 %** (10 %, 15 %…),
pour les emplacements comme pour la fenêtre de distance, et le tirage
recommence (`_place_black_cells`).

Les contraintes fortes : la case est encore blanche, ne **touche aucune
autre case noire**, ne fait pas tomber un emplacement touchant une lettre
verrouillée sous son seuil de candidats
(`_new_black_cell_breaks_locked_slot`, voir « Prise en compte des lettres
déjà verrouillées ») et laisse la grille structurellement valide avec
l'exigence de 6 cases. Cette dernière vérification donne toujours le même
résultat que `is_structurally_valid` sur la grille modifiée, mais sans la
reparcourir : zones de chaque ligne et colonne, cases isolées et points
d'articulation de la zone blanche sont calculés une fois par tirage
(`_BlackCellValidity`). Quand tous les emplacements sont retenus sans
qu'aucune case convienne (pourcentage à 100 %, toute la grille), le tirage
reprend à 5 % avec l'exigence abaissée d'un cran (5, 4, 3, 2, puis 1) :
le pourcentage est toujours augmenté avant que l'exigence soit baissée ; si même l'exigence de 1 case ne laisse aucune
candidate, plus aucune case noire n'est posée.

**Limite des emplacements courts.** Une fois l'objectif « Taux noir »
atteint, on compte les emplacements de **2 ou 3 lettres**
(`SHORT_SLOT_MAX_LENGTH`), horizontaux et verticaux, sans compter ceux qui
touchent un bord (leur longueur n'est pas contrainte). S'il y en a plus de
`SHORT_SLOT_MAX_COUNT` (**10**), les cases noires posées par ce tirage qui
les délimitent (la case juste avant ou juste après l'emplacement) sont
retirées, puis le tirage est relancé jusqu'à retrouver l'objectif ; le
compte est refait après chaque relance, au plus
`SHORT_SLOT_REDRAW_MAX_ROUNDS` (**10**) fois, après quoi la grille est
gardée telle quelle. Les cases noires du motif de départ et celles du
pré-remplissage ne sont jamais retirées par cette règle ; une case
retirée redevient candidate pour la relance (`make_pattern`,
`_short_slot_bounding_blacks`).

**Coins interdits au tirage.** Le tirage vers l'objectif « Taux noir » ne
pose jamais de case noire dans le carré de 2×2 cases de chacun des quatre
coins de la grille (`CORNER_SQUARE_SIZE`) : ces cases sont retirées de ses
candidates (`make_pattern`, `_in_corner_square`). Une case noire ne peut y
apparaître que par un autre mécanisme : le pré-remplissage, la reprise
entre paliers, la résolution des zones impossibles ou le réaménagement
d'une case noire flottante.

**L'adjacence n'est jamais acceptée par la génération de motif**, à aucun
palier : si aucune candidate isolée ne convient, plus aucune case n'est
posée.
Un palier peut donc finir avec moins de cases noires que visé ; la
recherche est tentée sur la grille telle quelle. Le pré-remplissage
(`_prefill_unfillable_slots`) suit la même règle : un emplacement qu'il ne
peut pas réparer ainsi se rabat sur le retrait d'un mot verrouillé qui le
croise, puis est marqué irréparable pour ce palier.

**Portée de cette interdiction.** Elle ne concerne que `make_pattern`. La
reprise entre paliers et la résolution des zones impossibles
(`_clean_blocked_slots`, `_build_retry_seed`, `_shorten_impossible_zones`/
`_lengthen_impossible_zones`, le réaménagement d'une case noire flottante)
peuvent poser ou déplacer une case noire adjacente à une autre.

### Le pré-remplissage

**Avant** le placement par pourcentage, tant qu'un emplacement a une
longueur comptant **moins de `PREFILL_MIN_WORD_COUNT` (3) mots candidats**
dans le dictionnaire (trop long, ou longueur trop rare), on pose des cases
noires — jusqu'à ce que ce ne soit plus possible, cas limite accepté. Ce
seuil de 3 est aussi celui des emplacements partiellement verrouillés
(ci-dessous) et de la priorité de sélection d'emplacement (chapitre 4).

#### Ces cases comptent dans l'objectif « Taux noir »

Le **Taux noir** (`black_enrichment_percent`, **15 %** par défaut) porte
sur **toute la grille** et vise un nombre total de cases noires : celles
du motif de départ du palier et celles du pré-remplissage y sont comptées,
et seule la différence est complétée (`make_pattern`). Une grille qui
repart d'un nettoyage ayant rouvert beaucoup de cases noires est donc
ramenée au taux réglé. Le même taux sert de budget par zone au nettoyage
curatif (`_prefill_unfillable_slots`).

#### Prise en compte des lettres déjà verrouillées

Quand un palier reprend un motif partiellement verrouillé (chapitre 5), le
pré-remplissage vérifie en plus, pour tout emplacement touchant une case
verrouillée, qu'il reste au moins **3** mots compatibles **avec ces
lettres à ces positions** (`PREFILL_LOCKED_MIN_WORD_COUNT`), pas
seulement avec sa longueur : un emplacement réduit à un seul mot est trop
fragile, le moindre conflit avec une lettre croisée le rendant impossible
sans recours.

La case noire corrective est choisie **parmi les cases de cet
emplacement**, sur celle qui touche la ligne/colonne la moins chargée en
cases noires (égalités au hasard), jamais ailleurs dans la grille. Si
aucune ne convient (par exemple deux mots croisés verrouillés qui ne
laissent aucune case libre pour le couper), l'emplacement est mis de côté
comme « impossible à corriger pour l'instant » et le pré-remplissage
continue avec les autres.

#### Le « nettoyage curatif »

Pour ce même cas (jamais pour une longueur simplement trop rare), on ne
noircit pas l'emplacement indéfiniment. Sa taille d'origine est relevée à
sa première détection, et le cumul des cases noires ajoutées pour lui est
comparé à son budget : le **Taux noir** appliqué à cette taille, mais
**jamais moins d'1 case** (`PREFILL_ZONE_BLACK_BUDGET_FLOOR`), le
pourcentage seul pouvant n'en autoriser aucune sur un emplacement de
taille normale (souvent 8 à 15 cases).

Budget dépassé (ou aucune case noire convenable), plutôt que de
sur-noircir une seule zone, on **retire un mot verrouillé qui le croise**
— forcément dans l'autre sens, donc participant aux lettres qui le rendent
difficile à remplir —, tiré au hasard parmi ceux qui le croisent, sans
critère de fragilité.
L'évaluation est répétée (case noire ou retrait de mot) jusqu'à retrouver
3 mots compatibles ; l'emplacement n'est marqué irréparable que si aucun
des deux leviers ne le débloque.

### La densité visée

Le pourcentage cible de ce placement (`black_ratio`, réglage du CLI) est
**0 % par défaut et ne progresse pas d'un palier à l'autre** : le
pré-remplissage et la reprise entre paliers (chapitre 5) suffisent à faire
progresser la grille sans la densifier artificiellement. Une
densification fixe s'applique à chaque palier partant d'une grille vierge
ou d'une simplification (jamais à une reprise « telle quelle ») : après le
pré-remplissage, le **Taux noir** (`POST_PREFILL_BLACK_FRACTION`, 0,10 par
défaut côté moteur) complète le nombre de cases noires jusqu'à ce
pourcentage de la grille entière.

### Réaménager une case noire flottante pour un mot Défi ou thématique

Un **Mot Défi** sans emplacement disponible de sa longueur peut s'en voir
façonner un en modifiant des **cases noires flottantes** — toute case
noire absente de `permanent_black_cells`. Les mots du glossaire
**thématique** y ont droit après les Mots Défi, tant que moins de
`THEME_RESHAPE_MAX_PLACED_WORDS` (**5**) mots thématiques distincts sont
posés (`_placed_theme_words`, `_theme_reshape_allowed`). Un mot est jugé
sans emplacement si aucun emplacement vide de sa longueur n'a des lettres
connues compatibles.

Un **réaménagement** est toujours lié à un seul mot et ne lui survit
jamais :

- **en génération automatique**, c'est une option du nœud de recherche sur
  l'emplacement qu'il vient de choisir : il mémorise l'état d'origine des
  cases noires modifiées, pose le mot, et les remet en état dès que le mot
  est refusé (« Réaménager l'emplacement choisi », chapitre 4) ;
- **en mode Interactif**, chaque tentative se fait sur une copie de la
  grille, et seule celle du mot réellement posé par **Suivant** est
  appliquée (« Le mode Interactif : poser un seul mot », chapitre 4).

Le mode Interactif cherche dans toute la grille une case noire à déplacer
ou un emplacement à raccourcir :

**L'élargissement.** Jusqu'à `WIDEN_BLACK_CELL_WINDOW` cases noires non
protégées sont examinées dans un ordre mélangé ; pour chacune est calculée
la zone blanche qui résulterait de son déplacement (`_white_run`). Si le
mot tient à ras d'une extrémité de cette zone, en respectant les lettres
connues, et que la grille reste structurellement valide
(`is_structurally_valid`, seuil relâché à 1), la case noire est déplacée
pour border le mot de l'autre côté (`_try_widen_black_cell`,
`_widen_one_floating_black_cell`).

**Le repli par raccourcissement** (`_shorten_one_slot_for_word`). Un
emplacement vide **strictement plus long** que le mot reçoit le mot à son
début ou à sa fin, et une case noire neuve juste après lui
(`_try_shorten_slot`). Au plus `SHORTEN_SLOT_WINDOW` emplacements par mot,
soit 10 % de la fenêtre d'élargissement
(`FALLBACK_PHASE_BUDGET_FRACTION`).

**Deux garde-fous** protègent les mots déjà posés ailleurs :

1. la nouvelle case noire n'est jamais une case portant une vraie lettre ;
2. dans l'axe **perpendiculaire** au mot, ni la case libérée (sa lettre du
   mot déjà appliquée) ni la case noircie ne peuvent laisser l'emplacement
   perpendiculaire qui les traverse sans aucun mot du dictionnaire possible
   (`_perpendicular_slot_stays_valid`/`_slot_has_domain`) : libérer une
   case pourrait accoler une case surnuméraire à un mot perpendiculaire,
   en noircir une pourrait le tronquer.

Grâce à eux, aucune de ces manipulations ne peut rendre un emplacement
impossible ailleurs dans la grille. C'est un mécanisme du mieux possible,
pas une garantie : un mot trop long pour la moindre zone voisine ou le
moindre emplacement plus long, ou pour lequel aucune manipulation ne reste
valide, retombe sur les chances ordinaires de placement, sans rien
corrompre. Le nombre de mots essayés est borné par
`WIDEN_PRIORITY_WORDS_LIMIT` (`_find_priority_word_placement`).

Côté interface, la grille entière renvoyée par le serveur remplace l'état
affiché : **Précédent** annule le mot et son réaménagement.

---

## Chapitre 4 — Phase 2 : remplir la grille avec de vrais mots

Chaque suite d'au moins 2 cases blanches est un emplacement à remplir
(`extract_slots`). Le remplissage se fait par **essais successifs avec
retour en arrière** (`Filler`/`_backtrack`, appelés par `try_fill`) : le
programme choisit un emplacement, y place un mot compatible avec les
lettres des mots croisés, puis passe au suivant ; si un emplacement ne
peut plus recevoir aucun mot (y compris quand tous ses mots compatibles
sont utilisés ailleurs), il annule le dernier mot posé et en essaie un
autre.

Deux règles absolues :

- **un mot n'apparaît qu'une fois dans la grille** (`Filler.used_words`) ;
- **aucun mot n'est posé s'il laisse un emplacement bloqué parmi ceux
  qu'il croise.** Un candidat est jugé sur l'**état qu'il laisse** :
  - s'il **rend bloqué** un emplacement croisé qui ne l'était pas : refusé,
    sauf en tout dernier recours (« L'unique dérogation ») ;
  - s'il **croise un emplacement bloqué** qui le reste : refusé à tous les
    stades. Un emplacement que le candidat débloque (sa lettre remplaçant
    une graine) n'est pas concerné.

Un emplacement écarté (jaune) redevenu sain se croise librement.

### Avant la recherche : déductions certaines, graines, emplacements condamnés

#### Les emplacements à une seule possibilité

Pour chaque emplacement jouable, si les lettres connues ne laissent
qu'**un seul mot du dictionnaire** (éventuellement un seul mot de cette
longueur), ses lettres sont figées sur ce mot
(`_force_single_candidate_slots`) — un fait acquis, pas une graine. Le
passage est répété jusqu'à ne plus rien déduire, une déduction pouvant en
entraîner une autre par croisement. Un emplacement connu impossible n'est
pas examiné.

#### Les graines

Pour chaque emplacement incertain, le programme tire
`LETTER_BIAS_SAMPLE_SIZE` (10) mots de la bonne longueur, parmi ceux
compatibles avec les lettres connues (`sample_letter_biases`), et relève
case par case la lettre la plus fréquente. Une case n'est candidate que si
cette lettre « consensuelle » est apparue plus de `LETTER_BIAS_MIN_COUNT`
(1) fois sur les 10 : un consensus trop faible ne garantit pas qu'il reste
assez de mots compatibles une fois la lettre figée.

Parmi les candidates, un certain nombre sont piochées **au hasard** comme
**graines** — des indices qui initient ou influencent les premiers
placements. Leur nombre va jusqu'à `force_letters_percent` (0 % par
défaut ; l'interface ne le propose pas, donc toute génération depuis la
page ou par Populate se fait sans graine) du nombre de cases blanches
**encore sans lettre connue** — pas du total des cases blanches : le
nombre de graines diminue donc à mesure qu'un palier de reprise confirme
la grille. Jamais plus d'une graine par emplacement (une case qui croise
deux emplacements compte pour les deux).

Une graine n'est jamais un mot posé : la vraie lettre d'un mot croisé
prend le pas sur elle, et une case connue avec certitude n'est jamais
reproposée. Elle réduit en revanche, comme une vraie lettre, le nombre de
candidats de son emplacement, ce qui lui donne une vraie priorité de
traitement dans la sélection ci-dessous. Un emplacement impossible n'en propose
jamais, faute de mot à tirer.

Le relevé est tenu **séparément pour chaque sens** (`sample_letter_biases`,
`Filler.letter_scores_by_dir`). Leur combinaison est leur croisement — les
lettres présentes dans les deux sens, chacune au plus bas de ses deux
décomptes (`_crossed_letter_counts`) — conservée par case
(`Filler.letter_scores`) : elle sert au classement des mots candidats, au
choix de l'emplacement (niveaux 7 et 9), au bouton **Stats** et aux
lettres grises des aperçus.

Le relevé suit la recherche. À chaque mot posé, les emplacements qu'il
**croise** sont rééchantillonnés sur leur domaine courant
(`Filler._refresh_letter_scores_around`), sauf ceux au domaine vide. Seul
le relevé du sens de l'emplacement rééchantillonné est remplacé, puis la
combinaison des deux sens est recalculée (lettres communes, au plus bas
des deux décomptes) : elle confronte toujours le dernier relevé de chaque
côté. Le rafraîchissement est défait avec la pose lors d'un retour en
arrière, pour qu'un relevé ne survive jamais à l'état sur lequel il a été
mesuré. Le coût est borné : au plus un emplacement croisé par case du mot
posé, et la recherche calcule déjà le domaine de chaque emplacement ouvert
à chaque étape.

#### Les emplacements condamnés dès le départ

Un emplacement sans aucun mot possible avant le premier mot (par exemple
une case noire coupant un emplacement partiellement verrouillé d'une façon
qui ne correspond à aucun mot) est repéré
juste avant la recherche (`Filler.mark_immediately_impossible_slots`) et
« écarté » ; la recherche remplit le reste. Ce n'est qu'une avance : la
vérification de domaine par nœud trouverait les mêmes dès son premier
appel.

### Le mécanisme de backtracking, en détail

La recherche est une fonction récursive, un emplacement à la fois
(`Filler._backtrack`). À chaque appel (un « nœud ») :

1. **Calculer les mots possibles de chaque emplacement ouvert**
   (`Filler._domain`) : le dictionnaire, indexé par longueur/position/
   lettre (`build_index`), donne instantanément les mots compatibles avec
   les lettres connues — d'un mot croisé posé, d'une lettre verrouillée,
   ou, en dernier recours, d'une graine. Un mot de `used_words` ne compte
   jamais.
2. **Un emplacement sans aucun candidat** est marqué **écarté** et **le
   nœud échoue aussitôt** : une pose antérieure en est responsable. Font
   exception les emplacements de `Filler._tolerated_dry`, seulement omis
   des domaines du nœud : ceux déjà vides avant toute pose de la recherche
   (`Filler.solve`, `_dry_open_slots`) et, dans la passe de dernier
   recours, ceux qu'un mot de dernier recours a vidés. Les emplacements
   qui croisent un emplacement toléré restent sélectionnables, mais aucun
   de leurs candidats n'est retenu tant qu'il reste bloqué.
3. **Choisir un emplacement** par la cascade à 9 niveaux, parmi les
   sélectionnables (« Les emplacements écartés »).
4. **Essayer les candidats un par un** :
   - **chaque candidat compte pour le budget de vérifications**, qu'il
     mène ou non à une descente ; le budget et un éventuel signal d'abandon
     sont consultés à ce moment, si bien qu'un emplacement dont presque
     tous les candidats cassent un croisement ne peut pas enchaîner des
     milliers de rejets sans contrôle ;
   - le mot est posé provisoirement et ajouté à `used_words` ;
   - les emplacements qu'il **croise** (`Filler._crossing_slots`) sont
     évalués : si l'un, encore ouvert et sain avant la pose, n'a plus
     aucun mot (`crossing_broken`), le mot est retiré sur-le-champ et le
     candidat suivant est essayé (« Sécurité des croisements ») ;
   - sinon le programme s'appelle récursivement ; un succès remonte tel
     quel, un échec fait retirer le mot et essayer le suivant.
5. **Si aucun candidat n'aboutit**, le nœud échoue et son appelant retire
   son propre mot : le retour en arrière peut remonter plusieurs
   emplacements d'un coup.
6. **Un nœud ne fait qu'un nombre limité de descentes** : après
   `MAX_DESCENTS_PER_NODE` (10) descentes sans succès, il échoue, quel que
   soit le temps du nœud atteint, dernier recours compris. Une descente
   est un candidat qui a passé le contrôle de croisement et dans lequel la
   recherche est descendue ; un candidat rejeté par ce contrôle n'en est
   pas une, ni un Mot Défi ou un mot thématique posé sur un emplacement
   existant (une option de réaménagement, elle, compte toujours). Sans
   cette limite, un nœud n'échouerait qu'après avoir épuisé son sous-arbre,
   ce qui n'arrive jamais dans le budget sur un vrai dictionnaire : le
   retour en arrière ne remonterait que de quelques niveaux, et un mot
   difficile posé tôt resterait en place toute la tentative. Avec elle, il
   remonte jusqu'aux premiers mots et peut les remplacer. Une valeur `<= 0` supprime la
   limite. Trois ajustements :
   - un nœud qui reçoit un saut arrière (point 7) — un échec ayant
     traversé au moins un nœud intermédiaire sans que celui-ci essaie ses
     autres candidats — n'a plus droit qu'à une descente supplémentaire :
     son plafond tombe au nombre de descentes déjà faites plus une. Il
     n'atteint le plafond complet que si tous les échecs qui lui reviennent
     sont des retours en arrière ordinaires, nés dans son propre nœud
     enfant (`Filler._fail`, `_last_jumped`) ;
   - un nœud atteint alors que la recherche a posé moins de
     `EARLY_DESCENTS_WORD_COUNT` (10) mots en plus de ceux de l'état
     initial peut faire `EARLY_MAX_DESCENTS_PER_NODE`
     (2 × `MAX_DESCENTS_PER_NODE`, soit 20) descentes, le compte étant
     pris à l'entrée du nœud ;
   - une grille héritée d'une étape précédente (lettres verrouillées, ou
     mots en place au lancement de `Filler.solve`) n'applique aucun
     plafond : elle doit être finie au mieux, et chacun de ses nœuds
     explore toutes ses possibilités (`Filler.solve`,
     `Filler._inherited`).
7. **Le retour en arrière saute à la cause (backjumping).** Un nœud qui
   échoue indique son **ensemble de conflit**, les mots posés à l'origine
   de son échec :
   - emplacement vide : les mots qui le croisent, plus ceux qui occupent
     un mot qu'il aurait pu prendre (`_dry_slot_conflict`) ;
   - emplacement dont tous les candidats bloqueraient un emplacement
     croisé sain : les mots qui croisent l'un et l'autre
     (`_assigned_crossers`) ;
   - nœud épuisé : la réunion des ensembles de conflit de tout ce qu'il a
     essayé, sans son propre mot.

   Un nœud dont le mot ne figure pas dans l'ensemble qui lui remonte retire
   son mot et transmet l'échec tel quel : essayer ses autres candidats
   rejouerait le même échec. Le retour en arrière traverse ainsi d'un coup
   tous les niveaux sans rapport avec le blocage, jusqu'au mot le plus
   récent réellement impliqué, qui essaie son candidat suivant. Sans ce
   mécanisme, les mots posés entre le blocage et sa cause seraient remis
   en cause un par un, à raison de `MAX_DESCENTS_PER_NODE` possibilités
   par niveau, le même blocage étant rejoué à chacune. Un échec dû au budget ou à
   un abandon n'indique aucun conflit (retour en arrière ordinaire) ; un
   emplacement vide avant toute pose ne met en cause aucun mot, donc son
   échec remonte à la racine (`Filler._fail`, `_last_conflict`,
   `BACKJUMPING_ENABLED`).
8. **Un saut arrière trop long est remplacé par un retrait fantôme
   (backghost).** Un saut arrière retire au plus `MAX_BACKJUMP_LEVELS` (5)
   mots posés par la recherche. Là où un échec naît avec un ensemble de
   conflit — un emplacement vide, un emplacement dont tous les candidats
   bloqueraient un emplacement croisé sain, un nœud épuisé ou arrivé à son
   plafond de descentes, jamais un échec simplement transmis —, la
   recherche regarde le
   mot le plus récent de cet ensemble parmi ceux qu'elle a posés
   (`Filler._placement_seq` ; les mots présents au lancement ne sont
   jamais retirés ainsi). Si plus de `MAX_BACKJUMP_LEVELS` mots ont été
   posés après lui, ce seul mot est retiré sur place : aucun nœud n'est
   dépilé, les mots posés depuis restent, les relevés de lettres des
   emplacements qu'il croisait sont rééchantillonnés, et un nouveau nœud
   reprend la recherche. Le nœud qui l'avait posé constatera plus tard
   qu'il n'a rien à retirer. À distance de `MAX_BACKJUMP_LEVELS` mots ou
   moins, l'échec déclenche le saut arrière ordinaire, qui dépile pour de
   bon. Si la reprise échoue, le même choix est
   refait sur la réunion des deux ensembles de conflit, sans l'emplacement
   retiré — nouveau retrait fantôme si son mot le plus récent est encore
   au-delà de `MAX_BACKJUMP_LEVELS`, saut arrière sinon : les mots en
   cause sont retirés un à un. Au plus
   `MAX_BACKGHOSTS_PER_DESCENT` (10) retraits fantômes imbriqués par
   descente ; au-delà, le saut arrière est fait en entier. Une valeur
   `<= 0` supprime le mécanisme. Un retrait fantôme fait sous un
   réaménagement est conservé quand celui-ci est défait
   (`Filler._fail_or_backghost`, `Filler._backghost_target`,
   `Filler._undo_reshape`).

Vérifier les seuls voisins directs suffit : un mot n'affecte le domaine
d'aucun emplacement qui ne partage pas de case avec lui.

#### Fin de la recherche

La recherche réussit quand chaque emplacement a reçu un vrai mot. Elle
échoue si la racine épuise ses candidats ou ses descentes, deux fois : en
recherche stricte, puis dans la passe de dernier recours (« L'unique
dérogation »). Sur une grille trop difficile, c'est le budget de
vérifications qui met fin à la tentative.

**Le budget compte les tentatives de poser un mot**, pas les appels
récursifs : chaque candidat essayé vaut une unité, même rejeté
immédiatement par le contrôle de croisement.

### Les emplacements écartés

Un **emplacement écarté** (fond jaune) est une pure **déprioritisation**,
propre à la tentative : un emplacement trouvé bloqué récemment est mis de
côté pour que la recherche n'y revienne qu'une fois qu'aucun autre ne peut
recevoir de mot. La liste ne garde que les **3 derniers**
(`MAX_EXCLUDED_SLOTS`, `_RecentSlots`) : un quatrième en fait sortir le
plus ancien, et un emplacement écarté de nouveau redevient le plus
récent — la liste doit désigner les endroits qui viennent de poser
problème, pas recouvrir la grille. **À chaque pose**, les emplacements écartés que
le mot croise sont réévalués par le contrôle de croisement : celui qui
n'est plus bloqué sort de la liste. Cette sortie n'est pas annulée si le
mot est retiré plus tard : l'emplacement est simplement écarté de nouveau
s'il redevient vide. Un emplacement écarté n'est jamais muré ni hérité d'un palier
précédent (`Filler._impossible_this_attempt` ; un nouveau `Filler` démarre
avec la liste vide).

Trois sources, internes à la recherche, l'alimentent : le balayage
préalable (`mark_immediately_impossible_slots`), la vérification de
domaine par nœud (étape 2), et l'épuisement des candidats d'un emplacement
dont aucun ne pouvait être posé sans créer d'emplacement impossible —
constat gratuit, la boucle de candidats venant de les essayer tous.

**Le constat provoque un retour en arrière, le marquage reste une
possibilité.** Un emplacement constaté impossible à remplir en l'état —
domaine vide, ou chaque candidat bloquant un emplacement croisé sain —
fait échouer le nœud. Ne font pas échouer le nœud : un emplacement dont
les candidats sont tous refusés **uniquement** parce qu'ils croiseraient
un emplacement toléré (le nœud passe au suivant), et, dans la passe de
dernier recours, l'épuisement des candidats. Le marquage, lui, survit au
retour en arrière : l'emplacement est peut-être redevenu viable, il est
simplement retenté après les autres (`blameable_rejection`). Son domaine
étant non vide, il continue d'être croisé librement, contrairement à un
emplacement bloqué (rouge). **Aucun calcul d'affichage n'écrit dans cette
liste** : un blocage croisé est signalé pour l'instantané où il est
constaté, jamais mémorisé : c'est une propriété de l'état examiné, que la
pose suivante peut dissoudre. Sinon la publication d'aperçus réécrirait
l'ordonnancement de la recherche jusqu'à ce que tout soit écarté et que la
déprioritisation ne veuille plus rien dire.

Chaque nœud se déroule en quatre temps :

1. poser un mot sur un emplacement **non écarté**, dans l'ordre de la
   cascade ;
2. à défaut, **libérer** les emplacements écartés, qui redeviennent
   ordinaires ;
3. à défaut, et seulement dans la seconde passe de la recherche, accepter
   un mot qui **rend bloqué un emplacement croisé** (croiser un
   emplacement déjà bloqué reste refusé) ;
4. sinon le nœud échoue.

Ils partagent le même plafond de descentes. La libération vaut pour toute
la descente qui suit et se défait en remontant (`released`, paramètre de
récursion).

**Remplissage incrémental** (`INCREMENTAL_FILL_ENABLED`, activé ;
`backend/crossword_gen.py`, `Filler._attention_pool`,
`Filler._widen_attention`) : à chaque palier (`generate_grid`), pour
toutes ses tentatives — grilles reprises comme grilles neuves —, les temps
1 et 2 se déroulent d'abord dans une **zone d'attention** : le rectangle des R premières
lignes et C premières colonnes, à partir de la case (0, 0). Seul un
emplacement ayant au moins une case encore libre (ni mot posé, ni lettre
verrouillée) dans ce rectangle peut recevoir une pose. R et C valent d'abord
`INCREMENTAL_FILL_START_SIZE` (6). Quand le nœud ne peut plus rien poser
dans la zone — plus aucun emplacement concerné, ou tous essayés, écartés
libérés compris —, la zone s'élargit de `INCREMENTAL_FILL_COL_STEP` (4)
colonnes tant qu'elle n'occupe pas toute la largeur de la grille, puis,
une fois la pleine largeur atteinte, s'allonge de
`INCREMENTAL_FILL_ROW_STEP` (4) lignes (`Filler._widen_attention`), et le
nœud reprend au temps 1 sur l'ensemble agrandi (les emplacements déjà
essayés par ce nœud le restent), jusqu'à couvrir toute la grille ; le
temps 3 ne vient qu'ensuite. La taille de la zone est un paramètre de
récursion comme `released` : héritée par la descente qui suit,
restaurée en remontant ; chaque racine (`solve()`, y compris après un
nettoyage précoce) repart de 6x6. Une tentative partie de lettres
verrouillées (palier 2 et suivants) qui les perd toutes à la suite d'un
nettoyage dur (précoce ou sur mot répété) ramène sa zone à 6x6, une seule
fois par tentative (`Filler._attention_after_unlock`) : le nœud qui
poursuit après le nettoyage sur mot répété repart de 6x6 ; après un
nettoyage précoce, la racine repart de 6x6 de toute façon et la
réinitialisation est simplement consommée. Un emplacement asséché, où
qu'il soit, provoque le retour en arrière habituel : seule la sélection
est limitée par la zone (`try_fill`, `incremental_fill`). Un emplacement écarté est repris dès que son domaine
redevient non vide (`_domain` est recalculé à chaque nœud) et cesse d'être
jaune dès qu'un mot y est posé.

**`Filler.excluded_slots` est un mécanisme distinct** : il retire un
emplacement de la grille à résoudre — jamais sélectionné, jamais exigé par
la réussite, jamais compté comme croisement cassé, jamais montré. Ses deux
utilisateurs sont `_optimize_before_cleanup` (chapitre 5) et « Finir la
zone », qui y place les emplacements ouverts hors de la zone (`try_fill`,
`_outside_zone_slot_indices`).

La liste n'a aucun effet en mode Interactif (`interactive_place_word`),
qui construit sa propre liste d'emplacements sélectionnables.

### Choisir quel emplacement remplir

Le choix suit une cascade de **niveaux de priorité**
(`Filler._select_target_slot`, réutilisé tel quel par
`interactive_place_word`), appliquée aux emplacements que les temps du
nœud rendent sélectionnables — au premier palier, ceux de la zone
d'attention du remplissage incrémental (voir « Les emplacements
écartés ») :

1. **désactivé** (`ALTERNATE_DIRECTION_ENABLED`) : activé, il tire d'abord
   la direction, avec une probabilité proportionnelle au nombre
   d'emplacements libres de chacune. Désactivé, le groupe de départ est
   l'ensemble des emplacements libres ;
2. **Mots Défi**, prioritaire sur tous les niveaux suivants : s'il existe
   un emplacement où un mot de la liste (ni posé, ni abandonné) tient
   encore, le choix s'y restreint. « Tient » est purement
   **géométrique** — même longueur, compatibilité avec les lettres
   réellement connues (jamais une graine) — sans exiger que le mot soit
   dans le dictionnaire (`Filler._challenge_word_fits`) ; un tel mot hors
   dictionnaire est ensuite signalé invalide par les diagnostics
   (`_invalid_fully_known_indices`), comme s'il avait été inséré à la
   main. Ce niveau est placé avant le niveau 3 : une grille avancée a
   presque toujours un emplacement à moins de 3 candidats quelque part, et
   le niveau 3 appliqué d'abord écarterait tout emplacement compatible
   Mots Défi. Sans mot dans la liste, ce niveau ne change rien ;
3. **pour les emplacements de 4 lettres et plus**, priorité à ceux ayant
   **moins de `PREFILL_MIN_WORD_COUNT` (3) candidats** (un emplacement de
   2-3 lettres a un vocabulaire naturellement restreint) : résoudre ces
   emplacements fragiles par un vrai mot avant qu'un nettoyage ne les juge
   insuffisants et n'y ajoute une case noire ;
4. **désactivé** (`KNOWN_LETTER_LEVEL_ENABLED`) : activé, il restreint le
   choix aux emplacements ayant **au moins une case déterminée par une
   vraie lettre** (mot croisé posé ou lettre verrouillée, jamais une
   graine) : finir un emplacement entamé plutôt que d'en ouvrir un
   nouveau ;
5. **grille thématique** : restriction aux emplacements où un mot du
   glossaire non encore posé tient encore, chaque direction étant jaugée
   contre le glossaire de sa langue. Venant après le niveau 2, il laisse
   la priorité aux Mots Défi ;
6. **fenêtre géométrique** : chaque emplacement reçoit pour score le carré
   de la distance (euclidienne) entre l'origine et sa **case la plus
   proche de l'origine** — pas son point médian. L'origine est le **milieu
   du segment reliant le centre de la grille**
   (`((lignes - 1) / 2, (colonnes - 1) / 2)`, `_slot_selection_origin`)
   **au centre du dernier mot posé par la descente en cours** (le plus
   grand `_placement_seq` encore sur la grille ; centre = milieu de sa
   première et de sa dernière case) : le remplissage progresse de proche
   en proche autour de chaque nouveau mot, ramené à mi-chemin vers le
   centre, sans rester cantonné au disque central ni tourner au hasard
   dans toute la grille. Un mot retiré par retour en arrière ou retrait
   fantôme ne compte plus. Tant que la descente n'a posé aucun mot (les
   mots présents au départ de `solve()` n'étant pas numérotés), l'origine
   est le centre de la grille. En mode Interactif, le dernier mot est le
   dernier posé par « Suivant » encore entièrement présent — un mot annulé
   par « Précédent » ou effacé à la main ne compte plus, on remonte au
   précédent
   (`Filler._selection_origin`, `Filler.last_placed_cells`,
   `_placed_word_origin_cells` ; `frontend/static/script.js`,
   `interactiveOriginCells`). Il suffit à un emplacement long d'approcher
   l'origine par une seule case pour obtenir un bon score ; la distance
   étant euclidienne, la fenêtre forme un cercle plutôt qu'un losange. Le
   score ne dépend pas de l'état de remplissage. On garde les
   `SLOT_SELECTION_WINDOW_SIZE` (10) emplacements au plus petit score,
   mélangés au préalable pour qu'aucun ordre positionnel ne départage les
   ex æquo ;
7. **case la plus contrainte** : dans cette fenêtre, on cherche le **plus
   petit nombre de lettres encore possibles sur une case libre**, et on ne
   garde que les emplacements possédant une case à ce compte. Ce nombre
   est celui du calcul **Stats** : les lettres communes aux relevés
   horizontal et vertical de la case (`Filler._slot_min_letter_options`,
   `_crossed_letter_counts`). Une case sans lettre commune compte 0 : ce
   n'est qu'un signal d'échantillonnage, elle est traitée en premier, et
   seuls les domaines réels (`Filler.slot_is_blocked`) établissent une
   case croisée bloquée. Une case d'un seul emplacement garde le relevé de
   ce sens ; une case déjà déterminée n'est pas comptée. La mesure suit un
   **seuil de longueur décroissant** : emplacements de **4 lettres et
   plus** (`MOST_CONSTRAINED_START_LENGTH`), puis 3, puis **2**
   (`MOST_CONSTRAINED_MIN_LENGTH`), le premier seuil retenant un
   emplacement mesurable étant appliqué : les longs emplacements sont
   résolus sur leur case la plus serrée avant les courts, dont les cases
   serrées reflètent surtout un vocabulaire restreint. La case la plus
   contrainte est celle où le remplissage peut échouer le plus tôt : on la
   résout pendant que la recherche a encore de la marge ;
8. la fenêtre est **retriée** par nombre de lettres déjà posées (vraies
   lettres, jamais une graine), puis **réduite** à sa première moitié
   (`SLOT_SELECTION_REFINE_FRACTION`, 1/2), mélangée d'abord. Le plancher
   est de 1 emplacement : la fenêtre issue du niveau 7 n'en compte souvent
   qu'un ;
9. elle est enfin retriée par un score statistique : la racine carrée de
   la somme des carrés des fréquences de la lettre la plus fréquente à
   chaque case **libre** — l'emplacement dont la zone propose le plus
   d'options de remplissage, donc le plus de lettres crédibles pour ses
   voisins —, **divisée par (1 + le nombre d'essais de l'emplacement)** :
   un emplacement souvent retenté cède la place à un moins exploré. Chaque emplacement mémorise, pour la tentative, les
   mots que la recherche y a posés et combien de fois (mots retirés
   compris) ; la mémoire est attachée à ses cases, donc conservée à
   travers un réaménagement. En mode Interactif, le compte est toujours
   nul (`Filler._record_tried_word`, `Filler._slot_try_count`). Le premier
   emplacement est choisi.

### Choisir quel mot essayer

Les candidats de l'emplacement sont **mélangés**, puis classés par leur
accord avec le consensus statistique des cases non déterminées
(`_candidate_score`, racine de la somme des carrés des scores par case),
score **divisé par (1 + le nombre de fois où ce mot a déjà été posé sur
cet emplacement)** (`Filler.ordered_candidates`, `Filler._tried_words`) :
un mot qui colle au consensus sur plusieurs cases est essayé avant un mot
qui n'y colle pas, et un mot déjà souvent retenté cède la place à un mot
neuf. En mode Interactif, ce compte est toujours nul.

À chaque tirage, seuls les `CANDIDATE_SCORE_WINDOW` (100) meilleurs
candidats **restants** sont considérés — une fenêtre glissante bien plus
étroite qu'un domaine de plusieurs milliers de mots, pour que le
classement statistique garde la main. Ils sont **retriés par fréquence**
dans `data/wordlist_<langue>_freq.tsv` (un mot absent comptant 0, les
ex æquo gardant l'ordre statistique), et le mot essayé est pioché au
hasard parmi les `CANDIDATE_FREQ_WINDOW` plus fréquents
(2 × `MAX_DESCENTS_PER_NODE`, soit 20) : un mot courant passe avant un mot
rare, avec assez de jeu pour que deux tentatives parallèles, ou deux
clics successifs sur un même état, ne convergent pas sur le même mot. Un
mot écarté par sa fréquence reste dans la fenêtre et sort quand elle se
vide. La
fréquence lue est celle du fichier, quelle que soit la difficulté
(`load_dictionary_frequencies`, `build_index` :
`index[longueur]["dict_freq"]`, en flottant 32 bits).

C'est la **règle unique de tirage d'un mot** (`Filler.ordered_candidates`),
partagée avec le bouton **Suivant** du mode Interactif, dont les trois
familles ordonnent ainsi les candidats de chaque emplacement et retiennent
le premier acceptable (`_general_dictionary_pick`).

Par-dessus ce classement, trois familles prennent la tête, en blocs
stables :

- **Mots Défi** — tout mot de la liste ni posé ni abandonné,
  géométriquement compatible, même hors dictionnaire. Un emplacement sans
  candidat du dictionnaire n'est pas une impasse tant qu'un tel mot lui
  reste compatible.
- **Glossaire thématique** — les candidats du glossaire avant les autres :
  un mot hors thématique n'est atteint que si aucun mot thématique n'a
  mené à une solution. Étape sautée si tous — ou aucun — des candidats
  sont thématiques.
- **Dictionnaire Scrabble** — parmi le reste, les mots du dictionnaire
  Scrabble de la langue de l'emplacement (une liste par direction sur une
  grille bilingue) avant les autres, l'ordre statistique étant conservé
  dans chaque bloc (`Filler.scrabble_first`, appliqué juste après
  `Filler.ordered_candidates` et avant le bloc thématique). Étape sautée
  si tous — ou aucun — des candidats sont dans la liste.

L'ordre complet des options d'un emplacement est : Mots Défi, Mots Défi
par réaménagement, mots thématiques, mots thématiques par réaménagement,
mots Scrabble, autres mots.

### Réaménager l'emplacement choisi pour un mot Défi ou thématique

En génération automatique, un nœud peut remplir son emplacement avec un
mot d'une **autre longueur** en modifiant les cases noires flottantes qui
le bornent (`Filler._reshape_candidates`, `_reshape_geometries`,
`_reshape_options`) :

- **mot plus court** : une case noire neuve juste après le mot, calé
  contre l'un des bouts ;
- **mot plus long** : la case noire bornant un bout est libérée,
  l'emplacement se prolonge sur les cases blanches suivantes, et une case
  noire neuve referme le mot si la zone ne s'arrête pas déjà là.

Y ont droit les Mots Défi actifs et, sous 5 mots thématiques posés, les
mots thématiques de la direction, non posés et sans emplacement vide
compatible de leur longueur — au plus `RESHAPE_WORDS_PER_NODE` (5) par
famille et par nœud, tirés au hasard. Une option n'est retenue que si le
mot concorde avec les lettres connues, qu'aucune lettre connue n'est
noircie, que la grille reste structurellement valide (seuil 1) et
qu'aucun emplacement portant un mot n'est modifié.

Le nœud mémorise l'état des cases noires modifiées et la liste
d'emplacements (affectation, emplacements écartés et tolérés, numéros de
pose sont reportés sur les nouveaux index), pose le mot, et contrôle les
emplacements qu'il croise et ceux que la modification a créés, ces
derniers comptant comme sains avant la pose (`Filler._try_reshape`,
`_apply_reshape`). Dès que le mot est refusé, tout est remis en état
(`_undo_reshape`) et l'ensemble de conflit est traduit sur les index
d'origine : une case noire déplacée ou ajoutée borde toujours un mot Défi
ou thématique posé.

Une option de réaménagement compte toujours comme une **descente**, et un
refus pour croisement compte dans le budget d'abandon du mot. Le mécanisme
est désactivé avec un `Filler.excluded_slots` non vide (donc pendant
« Finir la zone » dès qu'un emplacement ouvert est hors zone) ; seules les tentatives de
palier l'activent (`try_fill`, `reshape_black_cells`).

Un record (`best_assignment`) est mémorisé avec son motif et sa liste
d'emplacements : une recherche échouée reprend le motif de son record,
qu'elle rend au palier (`Filler.adopt_best_structure`).

### Sécurité des croisements et budget d'abandon

Quelle que soit sa famille, un candidat n'est **jamais laissé en place si
un emplacement qu'il croise est bloqué après lui** (plus aucun mot du
dictionnaire **et** plus aucun Mot Défi actif pour ce croisement) : il est
retiré sur-le-champ et le suivant est essayé. Le même mot est retenté sur
un autre emplacement au fil du backtracking, par les niveaux 2 et 5.

**La référence est gratuite.** Les domaines calculés en début de nœud
omettent tout emplacement bloqué : y figurer signifie « sain avant ce
mot ». Un emplacement croisé qui y figurait et n'a plus de mot a été
bloqué par le candidat (`crossing_broken` — refusé, sauf en dernier
recours) ; un emplacement qui n'y figurait pas et reste bloqué est un
emplacement bloqué que le mot croise (`crossing_still_impossible` — refusé
à tous les stades). Un emplacement déjà bloqué n'est jamais imputé au
candidat, et un emplacement écarté redevenu sain est protégé et croisé
comme tout autre.

#### L'unique dérogation, en tout dernier recours

Le critère « tout a été essayé » est **global** à la tentative
(`Filler.solve`) :

1. une **recherche stricte** depuis la racine, où aucun nœud ne peut créer
   d'emplacement bloqué ;
2. **seulement si la racine elle-même a échoué** (pas le budget, pas un
   abandon), la recherche est rejouée avec `Filler.breaking_permitted` :
   un nœud qui a épuisé ses possibilités strictes accepte alors un mot qui
   assèche un emplacement croisé.

La première passe ne s'épuise que si les possibilités sont peu
nombreuses : très petite grille, ou grille largement verrouillée. Sinon,
le budget s'épuise en recherche stricte : la tentative finit avec des
emplacements vides, et l'enrichissement de dernière chance puis le
nettoyage prennent le relais (chapitre 5). Mieux vaut une grille bien
remplie portant une zone impossible — que le nettoyage répare au palier
suivant — qu'une grille déclarée échouée très tôt, mais seulement une
fois que tout le reste a été tenté.

Cette passe ne relâche que le contrôle « ne pas rendre bloqué un
emplacement croisé encore sain ». Deux limites demeurent : **croiser un emplacement déjà bloqué reste
refusé** (seule la passe de dernière chance du chapitre 5 le permet), et
la dérogation n'est **jamais héritée** par un nœud inférieur. Les
candidats gardent leur ordre de priorité : un candidat sans danger reste
préféré et n'est dépassé qu'une fois son sous-arbre épuisé.

#### Le budget d'abandon par mot

- Un **Mot Défi** ou un **mot thématique** a son budget
  (`Filler._challenge_word_budget`/`_theme_word_budget`,
  `FALLBACK_PHASE_BUDGET_FRACTION` = 10 % du `deadline_checks`) : une fois
  qu'il a cassé un croisement autant de fois, il est abandonné pour la
  tentative (`_challenge_abandoned`/`_theme_abandoned`) — plus proposé, et
  un Mot Défi n'excuse plus un emplacement croisé sans mot. Le reste du
  budget va au reste de la grille ; le mot est retenté depuis zéro à la
  tentative suivante.
- Le **dictionnaire général** essaie simplement ses candidats restants. Un
  mot accepté par dérogation n'est décompté d'aucun budget.

### Qu'est-ce qu'un emplacement « impossible » ?

Trois cas, cumulatifs (rouge des aperçus, entrée du nettoyage du
chapitre 5).

**Plus aucun mot possible.** Le jugement se fonde sur les lettres d'un mot
croisé confirmé **ou verrouillées d'un palier précédent**, jamais sur une
graine. Les lettres verrouillées ne sont jamais ignorées : un emplacement
entièrement verrouillé ne formant aucun mot doit être signalé, sinon la
même combinaison se reconstruirait à chaque cycle
(`Filler.locked_letters`, distinct de `Filler.forced_letters`). Un
emplacement dont tous les candidats sont utilisés ailleurs relève de ce
cas.

**Le blocage croisé.** Deux emplacements ouverts qui se croisent sont
impossibles si leurs lettres encore atteignables à cette case (Mot Défi
disponible compris) n'ont **aucune lettre en commun**, même si chacun a
un domaine non vide. Un emplacement déjà vide de candidats n'est jamais
la cause d'un tel blocage (premier cas) (`Filler._crossing_deadlock_slots`
en recherche,
`_crossing_deadlock_indices` en dehors). La case du conflit s'affiche en
rouge vif (`deadlock_cells`, sous-ensemble des cases impossibles).

Ce cas n'est pas détecté à chaque nœud — le balayage parcourt le domaine
entier de chaque emplacement ouvert, soit des dizaines de milliers de mots
sur une grille peu remplie — mais à la cadence des instantanés
(`Filler.excluded_zone_cells(include_deadlock=True)`, appelée par
`_publish_new_best` et l'instantané final) : un blocage apparu puis résolu
entre deux instantanés n'est pas vu.

Le nettoyage ne noircit jamais une case pour un blocage croisé
(`_clean_blocked_slots`) : il ne fait que retirer les mots croisants.
Quand les deux emplacements sont vides — le cas le plus fréquent — il n'y
a rien à retirer : la paire reste signalée jusqu'à une modification
manuelle, ou jusqu'à ce que la génération automatique remanie son motif.

**Le quota de qualité de contenu** (génération automatique) : une grille
entièrement remplie est refusée si elle compte trop de noms propres
(`MAX_PROPER_NOUNS`) ou de mots sans glose (`MAX_NON_GLOSS_WORDS`), selon
la difficulté (`try_fill`) ; les mots Scrabble n'y entrent pas. Tous les
mots fautifs présents (pas seulement l'excédent) sont alors signalés
impossibles — mêmes cases rouges, même entrée pour le nettoyage
(`_quota_overflow_slot_indices`) — plutôt que de laisser la tentative
paraître « propre » tout en étant marquée échouée.

#### Rouge, jaune : deux signaux distincts

`Filler.impossible_zone_cells()` (rouge) ne traite jamais un emplacement
écarté comme impossible sur cette seule base : le retour en arrière le
rend régulièrement viable à nouveau. `Filler.excluded_zone_cells()`
lui donne son signal, le fond jaune (`.attempt-preview-grid
.cell.white.excluded`), jamais soustrait du rouge ; l'ordre de la cascade
CSS (`.low-candidates`, `.excluded`, puis `.noise`/`.impossible`/
`.deadlock`) laisse gagner le plus sévère : le jaune passe devant l'orange
de l'emplacement pauvre, derrière le violet et le rouge. L'affichage rend exactement la
liste des écartés encore non assignés.

**Portée.** La liste est peuplée sur trois publications : les rappels en
direct de `try_fill` (`_publish_new_best`/`_publish_live_state`), la
vignette « juste terminée », et `last_examples`, l'entrée d'historique de
fin de tentative (`pattern_attempt_failed`/`pattern_found`), publiée avant
l'optimisation et le nettoyage — elle porte le signal en plus du canal
`live_preview`, dont l'affichage peut prendre du retard en mode rapide.
Elle est vide
sur tout ce qui est publié ensuite (début de cycle suivant, optimisation).

### Limites de la recherche

#### Le budget de vérifications

Par défaut **largeur × hauteur × 2000** vérifications (300 000 en 15×10 ;
`try_fill`), une vérification étant une tentative de poser un mot. Depuis
l'interface, le **Mode** fixe ce budget par tentative, de 1 000 à
20 000 000 (`backend/app.py`, `BUDGET_MODES`). **Flash** (1 000) est le
plus fragile : le budget peut s'épuiser avant qu'une recherche
remplissable n'aboutisse — compromis assumé.

#### Un budget qui s'étire tant qu'une autre tentative en a besoin

Une tentative qui atteint son budget continue tant qu'une autre du même
palier tourne sans avoir atteint le sien : l'arrêter laisserait son
processeur inoccupé (`Filler._deadline_reached_without_extension`/
`_siblings_still_racing`, lisant deux tableaux partagés : le compteur de
vérifications de chaque tentative, et un octet disant si elle tourne
encore — le compteur seul ne distingue pas « en course » de « arrêtée
tôt »). La vérification a lieu au dépassement, puis toutes les 500
vérifications, rythme auquel chaque tentative rafraîchit sa case. Dès qu'aucune autre ne tourne sous son budget, le verdict
« stop » est **définitif** (`_deadline_extension_denied`) : transitoire,
il ne rejetterait qu'un candidat, la boucle en posant un autre juste
après sans jamais dérouler la recherche. Une tentative sans sœurs (mode
Interactif, minimisation, CLI) s'arrête à son budget.

#### Le pourcentage de budget affiché en direct

La ligne de statut affiche le budget consommé **en moyenne** par les
tentatives du palier (« … — 42 % des générations »), republié toutes les
2 secondes (`BUDGET_PROGRESS_REPORT_INTERVAL_S`). Chaque tentative met à
jour sa case d'un tableau partagé (remis à 0 à chaque palier) toutes les
`CHECKS_PROGRESS_REPORT_INTERVAL` (500) vérifications, indépendamment de
tout record.

Toutes les actions périodiques (cette mise à jour, le battement de cœur,
le bouton Stop, l'arrêt par une sœur) se déclenchent sur un **seuil
écoulé**, jamais sur un multiple exact du compteur, à l'entrée de
`_backtrack` et après chaque candidat compté
(`Filler._periodic_checkpoints`, `Filler._checkpoint_due`) : la plupart
des candidats étant rejetés sans descendre, le compteur enjamberait
n'importe quel multiple exact pendant de longues séries de rejets.

Aucun pourcentage n'est plafonné à 100 %, le budget étant élastique (une
tentative de remplacement, elle, s'arrête à 100 %). La
**moyenne** est affichée, jamais le maximum : une tentative en difficulté
ne fait pas croire que tout le palier a épuisé son budget.

Chaque grille de l'aperçu affiche aussi son pourcentage **propre**, relu
toutes les 2 secondes pour une tentative en calcul
(`_refresh_computing_budget_percents`), figé pour une tentative arrêtée,
à 0 % pour un début de cycle.

#### La grille de l'aperçu continue de bouger même sans nouveau record

Toutes les `LIVE_STATE_HEARTBEAT_INTERVAL` (5 000) vérifications, l'état
**courant** de chaque tentative est republié (`Filler.on_live_state`,
`"kind": "heartbeat"`), pour qu'un long plateau ne ressemble pas à un
blocage alors que le programme pose et retire de nombreux mots. Plus rare
que le pourcentage (il coûte la construction d'une grille), ce battement
ne recalcule pas les blocages croisés et n'entre **jamais** dans le vivier
de sélection de fin de palier : il peut être moins complet que le dernier
record.

#### Abandon anticipé au-delà de 3 emplacements impossibles (désactivé)

Une tentative peut être abandonnée dès que plus de
`UNFILLABLE_ABANDON_SLOT_COUNT` (3) emplacements sont impossibles (vérifié
toutes les 500 étapes). Mécanisme **désactivé**
(`UNFILLABLE_ABANDON_ENABLED`).

### Fin de la recherche : fermer les emplacements implicites

Quelle que soit l'issue, un dernier passage confirme les emplacements non
assignés auxquels il ne reste qu'un seul mot disponible compte tenu des
lettres des mots croisants (`_close_implied_slots`, appelée par
`try_fill`), répété jusqu'à un point fixe. Sans lui, une grille
visuellement complète pourrait être comptée échouée parce qu'un ou deux
emplacements implicitement déterminés n'ont jamais été choisis. Aucun mot
deviné n'y est placé : seule la dernière possibilité restante est
confirmée.

- Contrairement aux « emplacements à une seule possibilité » (avant la
  recherche, sur les seules lettres verrouillées), il tient compte des
  mots placés pendant la tentative : un mot déjà utilisé ailleurs n'est
  jamais confirmé une seconde fois.
- La confirmation peut écrire de vraies lettres : elle est **refusée** si
  elle laisserait bloqué un emplacement croisé ouvert. Y renoncer ne coûte
  rien (la grille n'était complétable dans aucun des deux cas) et laisse
  le nettoyage du chapitre 5 agir sur de vraies cases vides.

### Affichage : lettres verrouillées vs. graines statistiques

Cases verrouillées (contenu confirmé, porté d'un palier au suivant) et
cases forcées (graines) sont deux ensembles disjoints dans tout aperçu :
`build_partial_letters_grid` les superpose mais ne renvoie que les vraies
graines comme « cases forcées », seules à porter le liseré bleu.

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

**Un emplacement cible par famille.** Chaque famille désigne son propre
emplacement cible, par la cascade de choix d'emplacement évaluée par
rapport au seul glossaire qu'elle applique (`Filler._select_target_slot`,
paramètres `challenge_level`/`theme_level`) : la famille Mots Défi avec le
niveau 2 seul, la famille thématique avec le niveau 5 seul, le dictionnaire
général sans aucun des deux. Quand une famille échoue, la suivante réévalue
donc les emplacements candidats au lieu d'hériter d'emplacements choisis
pour un glossaire qu'elle n'applique pas. Chaque cible est calculée une
seule fois par clic, à la première utilisation, et réutilisée à chaque
niveau de tolérance (`interactive_place_word`, `_tier_target`).

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
2. seulement ensuite, pour la famille Mots Défi, et pour la famille
   thématique tant que moins de 5 mots thématiques sont posés sur la
   grille (au-delà, elle s'arrête à la phase 1), un mot qui ne correspond à **aucun**
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
emplacements encore ouverts dans l'ordre de la cascade, rejouée sans les
niveaux 2 et 5 — sa propre cible d'abord, puis les autres
(`_cascade_slot_order`) — et non ce seul emplacement : un
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
Les emplacements écartés pendant ce balayage — au plus les
`MAX_EXCLUDED_SLOTS` (3) derniers, comme dans la recherche automatique
(`_RecentSlots`) — sont renvoyés au panneau
(`excluded_cells`) et affichés en fond jaune, comme dans les
prévisualisations de la génération automatique. Chaque clic renvoie aussi
les **emplacements candidats** de la sélection qui a fourni l'emplacement
posé — pour le dictionnaire général, celle du balayage qui a fourni cet
emplacement (`_cascade_slot_order` renvoie chaque emplacement avec sa
fenêtre), et non la cible initiale quand le balayage a dû la dépasser ;
pour les Mots Défi et le glossaire thématique, la cible de la famille qui a
posé le mot (de la dernière famille essayée si rien n'est
posé) : la fenêtre géométrique du niveau 6 de la cascade (au plus
`SLOT_SELECTION_WINDOW_SIZE` emplacements, mémorisée par
`Filler._select_target_slot` dans `Filler.last_selection_window`), chacun
représenté par sa ou ses cases les plus proches de l'origine du niveau 6 —
celles qui lui donnent son score (`window_cells`,
`backend/crossword_gen.py`, `_origin_closest_cells`) —, entourées en bleu
dans le panneau. Sur chaque emplacement
balayé, les candidats sont tirés par la même règle unique de tirage que la
recherche automatique (`Filler.ordered_candidates` — mélange, classement
statistique, fenêtre glissante retriée par fréquence, tirage, voir « Choisir quel mot
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
  noire ; le nettoyage dur (chapitre 5) s'applique à **Nettoyer** de la
  même façon qu'au nettoyage automatique.
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

Dès la récolte du palier, avant de compter ses réussites, un dernier
recours est tenté sur chacune de ses tentatives échouées : si tout ce qui
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
tentative devient une **réussite** ordinaire, exactement comme un
remplissage abouti : elle compte pour le minimum de réussites
(`MIN_SUCCESSFUL_ATTEMPTS`), puis est optimisée et comparée aux autres
réussites — jamais acceptée seule. Sinon, rien n'est modifié et la
tentative reste un échec.

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

**Nettoyage dur (*hardclean*).** Option activée par défaut
(`HARD_CLEAN_ENABLED`) : une fois les retraits faits, **toutes** les lettres
des mots retirés sont effacées, y compris une lettre qu'un mot retiré
partageait avec un autre mot qui, lui, ne croise pas l'emplacement
impossible. Ce second mot, partiellement effacé, est retiré entièrement à
son tour (avec la case noire qui lui était associée, comme tout retrait) :
ses autres lettres sont effacées aussi, et déverrouillées si elles étaient
verrouillées, sauf là où un mot entier restant les porte encore. Sans
l'option, les lettres partagées restent en place,
portées par le mot non croisant. Le nettoyage dur opère par **emplacement
impossible**, pas seulement par mot : toute lettre présente sur un
emplacement impossible est effacée aussi, qu'un mot la porte ou non (lettre
laissée par un nettoyage antérieur, lettre verrouillée), de sorte que
l'emplacement redevient libre ; un emplacement impossible résolu par une
case noire posée dessus n'est pas concerné. Une lettre posée par l'utilisateur
(`permanent_locked_letters`) n'est jamais effacée. L'option vaut pour tous
les nettoyages : reprise « telle quelle », nettoyage complet et nettoyage
profond, et bouton **Nettoyer** du mode Interactif, qui passent tous par le
même code (`_clean_blocked_slots`).

Le nettoyage de fin d'étape n'est pas une procédure à part : c'est celle
du nettoyage dur précoce (ci-dessous) et de la seconde chance. Tous
laissent le même état : les mots entiers restants, plus les lettres
verrouillées de la tentative (`_diag_locked_letters`, tirées de
`diag["locked_letters"]`) que le nettoyage n'a ni effacées ni noircies.
Une lettre verrouillée effacée est déverrouillée, et une lettre qu'aucun
mot entier ni aucun verrou ne porte plus (lettre orpheline) est effacée
(`_clean_blocked_slots`, `_clean_continue_candidate`, `_build_retry_seed`,
`_cleaned_playable_score`, `interactive_clean_impossible_zones`).

Les deux chemins de reprise ne diffèrent que par leur nettoyage, jamais par
la façon dont le palier suivant démarre : chacun transmet au palier suivant
**toutes** les lettres que son nettoyage a confirmées, qu'un mot entier les
porte encore ou non. La reprise « telle quelle » les passe comme lettres
verrouillées (`_pattern_continue`, paramètre `locked_letters`, tiré du
`confirmed` de son propre nettoyage, transporté par `_continue_seed_pool`),
exactement comme le nettoyage complet le fait déjà (`_pattern_attempt`) ;
son `preseed_assignment`, qui ne porte que des mots entiers, ne suffirait
pas à les transmettre. Le bouton **Continuer** les conserve aussi
(`_serialize_resume_state`, champ `continue_locked_letters`). L'aperçu de
début de cycle les montre : il reçoit ce même `confirmed`, qui contient
toutes les lettres portées par le palier, et non le seul découpage en mots
entiers (`_cycle_start_preview` ne lit qu'une de ses deux formes de
reprise, jamais les deux).

**Nettoyage dur précoce (*early hardclean*).** Le nettoyage dur n'attend
pas toujours la fin d'une tentative. Dès qu'un nouveau record de sa
recherche (`best_assignment`) laisse au moins `EARLY_HARDCLEAN_PERCENT`
(10 %) des cases de la grille dans des emplacements impossibles (le total
compte toutes les cases, noires comprises ; 100 désactive le mécanisme), la
grille subit, **à l'intérieur même de la tentative**, le même nettoyage dur
que pour la seconde chance (`Filler._early_hardclean`, qui appelle
`_clean_blocked_slots` : aucune case noire ajoutée, déplacée ni rouverte).

À ce stade la grille est saturée, et l'historique des descentes n'a plus
d'utilité : il est effacé. Tous les nœuds de la recherche se défont, chacun
annulant ses propres modifications comme lors d'un abandon
(`Filler._restart_pending`, sans retrait fantôme), jusqu'à `Filler.solve`.
Celle-ci reprend alors la grille **à plat** dans l'état du record
(`Filler._restart_from_record` : motif et emplacements du record, ses mots,
statistiques de lettres ré-échantillonnées autour de chacun), lui applique
le nettoyage dur, et recommence le retour arrière de zéro depuis cet état,
qui devient la nouvelle racine de la recherche (passe stricte d'abord, puis
passe de dernier recours si elle est épuisée). Les mots de cette racine ne
sont plus jamais défaits par le retour arrière ni par un retrait fantôme :
seul un nettoyage dur précoce ultérieur peut les retirer. La profondeur de
la récursion reste ainsi bornée par les emplacements ouverts d'une seule
racine, au lieu de s'empiler à chaque nettoyage. Une reconfiguration de
cases noires que portait le record reste dans le motif, même si le
nettoyage retire le mot pour lequel elle avait été faite.

La tentative n'est ni déclarée
échouée, ni terminée, et le palier n'est pas quitté : elle garde son
processus, son numéro de grille, son générateur aléatoire, son budget de
vérifications, l'historique des mots essayés (`_tried_words`) et les
plafonds de descentes fixés à son premier départ, et s'achève par ses issues
habituelles (réussite, budget, interruption par une tentative sœur, bouton
Stop, ou un échec ordinaire, suivi alors de la seconde chance et du
nettoyage de fin de palier comme pour toute tentative).

Le nettoyage ne verrouille rien. Une lettre verrouillée qu'il efface est
déverrouillée, pour pouvoir être remplie à nouveau ; une lettre verrouillée
qu'il n'efface pas reste verrouillée ; une lettre qui n'était pas
verrouillée le reste. Une grille d'un premier palier, sans lettre
verrouillée héritée, n'en reçoit donc aucune. Le nettoyage ayant lieu à la
racine, aucun nœud n'est en cours : chaque mot retiré disparaît simplement
de la grille. Une lettre
qu'aucun mot complet restant ni aucun verrou ne porte plus (lettre
orpheline) est effacée. Le record repart de l'état nettoyé, publié
comme tout record. Les aperçus lisent les lettres verrouillées courantes de la
recherche (`try_fill`).

Le contrôle a lieu au départ de chaque recherche et de chaque reprise à
plat, sur l'état de la racine (`Filler.solve`), puis à chaque nouveau
record, seul instant où le record vit à coup sûr sur les emplacements et le
motif courants, une reconfiguration pouvant être active ensuite
(`Filler._early_hardclean_due`, `Filler._backtrack`). Un état nettoyé identique à un état déjà produit plus
tôt dans la même tentative (motif et lettres) désactive le mécanisme pour
le reste de cette tentative, un même nettoyage répété ne pouvant rien
apporter ; la recherche continue alors depuis cet état. Seules les
tentatives de génération (`_pattern_attempt`, `_pattern_continue`) s'en
servent, pas le mode Interactif.

**Nettoyage dur sur mot répété.** Un nettoyage dur se déclenche aussi
quand la recherche d'une tentative de génération pose **le même mot au même
emplacement plus de `MAX_SAME_WORD_PLACEMENTS` (1000) fois de suite**, sans
qu'aucun autre mot n'y ait été posé entre-temps : seul le dernier mot posé
sur chaque emplacement est retenu, avec son décompte, qui repart de 1 dès
qu'un autre mot y est posé (`Filler._last_word_streak`,
`Filler._record_tried_word`). Ce mot est alors devenu obligatoire dans une
boucle qui rejoue le même scénario. Au moment où il y est posé pour la
1001ᵉ fois de suite, l'emplacement est déclaré impossible et nettoyé
**sur place**, à l'intérieur de la recherche (`Filler._descend`,
`Filler._repeat_hardclean`) : `_clean_blocked_slots` reçoit cet emplacement
pour seul emplacement impossible, son propre mot retiré d'abord ; son mot,
les mots qui le croisent et toute lettre restée sur lui sont effacés, avec
les mêmes règles de verrouillage et de lettres orphelines que le nettoyage
dur précoce, sans toucher aux cases noires. Comme pour un retrait fantôme,
la pile de retour arrière est conservée : aucun nœud n'est défait, les mots
retirés quittent la grille et y restent absents, le nœud qui avait posé
chacun d'eux ne trouve plus rien à retirer quand la recherche revient à
lui, et un nouveau nœud poursuit la recherche depuis l'état nettoyé, dans
la même zone d'attention (ramenée à 6 si le nettoyage vient de retirer la
dernière lettre verrouillée, voir « Remplissage incrémental »).
L'emplacement devient un **emplacement écarté**
et son décompte repart de zéro. Les statistiques de lettres recalculées
pour les retraits sont restaurées si ce nouveau nœud échoue. Le record
n'est pas modifié. Seules les tentatives de génération (`_pattern_attempt`,
`_pattern_continue`, paramètre `same_word_limit` de `try_fill`) s'en
servent.

Le nettoyage dur laisse davantage d'emplacements libres, donc la reprise
« telle quelle » reste possible plus longtemps : les paliers à motif neuf —
les seuls qui posent des cases noires, et donc les seuls à produire
l'aperçu « Motif de cases noires posé » — se raréfient d'autant, jusqu'au
plancher imposé par `MAX_CONSECUTIVE_CONTINUE_PALIERS` (un motif neuf au
moins tous les 5 paliers).

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
  moins 6 cases ne s'applique qu'à la *pose* des cases noires, jamais à ce
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
passée par l'optimisation dédiée (chapitre 5). Quand la recherche aboutit,
l'historique navigable reçoit encore, juste avant les grilles réussies en
cours d'optimisation, le **dernier état des recherches** : la dernière
vignette en direct de chaque processus, telle qu'elle s'est figée (or,
orange ou bleu clair), avec son numéro de Process et son budget consommé
(`backend/app.py`, `_run_generate_job`, étape `search_final`). Chaque
entrée de l'historique mémorise aussi le nombre de grilles réussies à ce
moment (`success_count`), que le badge médaille affiche quand on la
consulte (`frontend/static/script.js`, `showEntrySuccessMedal`). Les
grilles réussies portent, elles aussi, le numéro de Process de la
tentative qui les a trouvées (`backend/crossword_gen.py`, `generate_grid`,
attribué à la récolte de chaque résultat).

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
- **Lettres cyan sombre** (mot du dictionnaire Scrabble) : la case
  appartient à une suite complète de lettres (horizontale ou verticale)
  qui forme un mot de la liste Scrabble de sa langue. Calculé sur la
  grille de chaque aperçu, quel que soit le chemin qui y a posé les
  lettres (`backend/app.py`, `_annotate_scrabble_cells` ;
  `backend/crossword_gen.py`, `scrabble_word_cells`). Les couleurs des
  mots thématiques (magenta) et des Mots Défi (vert) l'emportent sur une
  case partagée. En mode Interactif, la même couleur marque le mot posé
  par **Suivant** depuis le dictionnaire général quand il figure dans la
  liste Scrabble (`interactive_place_word`, `placed.from_scrabble`).
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
- **Cadre gras en pointillés** (zone d'attention) : le carré des N premières lignes et colonnes où la recherche peut encore
  poser un mot (voir « Remplissage incrémental », chapitre 4), à sa taille
  du moment (`Filler.attention_size` pour les aperçus en direct,
  `Filler.best_attention_size` pour l'état record ; champ
  `attention_size`). Présent sur les mêmes aperçus que les lettres
  statistiques ; absent dès que la zone couvre toute la grille (`frontend/static/script.js`, `renderAttemptPreview`).

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
(1..N), que ce palier parte d'une grille vierge ou d'une grille reprise
(« Finir la grille », « Finir la zone », « Continuer ») : dans ce second
cas toutes les tentatives repartent de la même grille, mais chacune est
une lignée distincte, affichée sous son propre numéro
(`backend/crossword_gen.py`, `_build_dispatch_lineage`). À chaque palier suivant, chaque tentative non réinitialisée hérite
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
