# Comment CrossWordFalcon construit une grille

Ce document décrit, en termes simples, l'algorithme de `backend/crossword_gen.py`
qui construit une grille de mots croisés remplie. Il se déroule en trois étapes :
d'abord placer les cases noires, puis remplir les cases blanches avec de vrais
mots, puis simplifier la grille — que ce soit une tentative bloquée qu'on
récupère avant de continuer, ou une grille déjà réussie qu'on rend encore plus
dense en retirant le plus de cases noires possible.

## Étape 1 — Placer les cases noires

On part d'une grille entièrement blanche de `width` colonnes sur `height`
lignes (15×10 par défaut), et on y ajoute des cases noires une par une, **de
façon totalement indépendante** — sans contrainte de symétrie. Ça permet
d'atteindre des motifs beaucoup plus clairsemés (donc des grilles avec
beaucoup plus de lettres visibles) tout en respectant les règles
structurelles ci-dessous.

### Pré-remplissage

**Avant même de commencer** le placement décrit plus bas, une phase de
**pré-remplissage** s'exécute : tant que la grille comporte un emplacement
(un mot à trouver) dont la longueur a **moins de 3 mots candidats** dans le
dictionnaire — typiquement un emplacement trop long, ou d'une longueur trop
rare, pour le dictionnaire — on continue à poser des cases noires jusqu'à ce
que ce ne soit plus le cas (ou jusqu'à ce qu'il ne soit plus possible d'en
ajouter, un cas limite accepté, pas une erreur). Ce seuil de 3 mots est le
même que celui utilisé par le critère d'impossibilité de remplissage
appliqué aux emplacements touchant déjà des lettres verrouillées (voir plus
bas).

Les cases posées durant ce pré-remplissage comptent pour l'objectif de
pourcentage de cases noires visé (le champ "Taux noir" de l'interface,
`black_enrichment_percent`, fixé à 14 % par défaut) : ce pourcentage est
calculé une bonne fois pour toutes sur le nombre de cases blanches **avant**
le pré-remplissage, puis comparé au nombre de cases déjà posées
(pré-remplissage inclus) — si le pré-remplissage a, à lui seul, déjà posé
plus de cases que ce pourcentage n'en réclame, aucune case supplémentaire
n'est ajoutée pour cette raison ; s'il en a posé moins, seule la différence
est complétée ensuite.

Ce pourcentage "Taux noir" est lui-même multiplié par la proportion de
cases blanches encore restantes (cases blanches / cases totales de la
grille), mesurée sur le motif de départ de ce palier précis (celui reçu du
palier précédent, s'il y en a un) avant que le pré-remplissage de ce
palier ne démarre. Pour la toute première grille (entièrement blanche),
cette proportion vaut 1 : le taux appliqué est donc rigoureusement le taux
réglé dans l'interface. À partir du deuxième palier, à mesure que les
cases noires s'accumulent d'un palier à l'autre, cette proportion — et
donc le taux réellement appliqué — diminue mécaniquement.

Ce même pré-remplissage tourne aussi, avec un contrôle en plus, chaque fois
qu'un palier reprend un motif déjà partiellement verrouillé (voir l'étape 3) :
en plus de vérifier la longueur d'un emplacement, il vérifie alors, pour tout
emplacement touchant au moins une case déjà verrouillée par une lettre
confirmée, qu'il reste vraiment au moins 3 mots compatibles **avec ces
lettres précises à ces positions précises** — pas seulement avec sa longueur
en général (un seuil plus bas que celui de la longueur seule, ci-dessus : un
emplacement réduit à un unique mot compatible se révèle trop fragile, le
moindre conflit avec une lettre croisée le rendant impossible sans recours ;
3 offre une marge de manœuvre minimale tout en restant nettement plus
permissif que le seuil de longueur seule). La case noire ajoutée pour
corriger un tel emplacement est
choisie **directement parmi les propres cases de cet emplacement** (jamais
ailleurs dans la grille au hasard), sur la case qui touche la ligne/colonne
la moins déjà chargée en cases noires (les cases à égalité étant départagées
au hasard). Si aucune case de l'emplacement à corriger ne convient (un vrai
blocage, par exemple deux mots croisés déjà verrouillés qui ne laissent plus
aucune case libre pour le couper), cet emplacement est mis de côté comme
"impossible à corriger pour l'instant" et le pré-remplissage continue avec
les autres emplacements encore à court de candidats.

**« Nettoyage curatif »** : pour ce même cas (un emplacement rendu
insuffisant par des lettres déjà verrouillées — jamais pour le cas d'une
longueur simplement trop rare, qu'aucun retrait de mot ne pourrait de toute
façon corriger), on ne se contente plus de continuer à noircir cet
emplacement indéfiniment. À sa toute première détection, on compte combien
de cases blanches il couvre (sa taille d'origine) ; à chaque nouvelle case
noire ajoutée pour le corriger, on la mémorise et on compare le nombre
cumulé de cases ainsi ajoutées à cette taille d'origine. Tant que ce cumul
reste sous le budget de cet emplacement — l'**objectif de remplissage en
noir de la grille entière** (le même "Taux noir" cité plus haut, 17 % par
défaut) appliqué à sa taille d'origine, mais **jamais moins de 2 cases
noires garanties** — on continue d'ajouter des cases noires normalement.
Ce plancher garantit qu'un emplacement de taille normale (souvent 8-15
cases) dispose toujours d'au moins 2 cases avant que le pourcentage ne
prenne le relais — un pourcentage appliqué directement à la taille d'un
petit emplacement (9 cases ou moins à 10 %, par exemple) pourrait sinon
n'autoriser aucune case noire du tout.

Une fois ce budget dépassé (ou si aucune case noire disponible ne
convient), plutôt que de déclarer aussitôt l'emplacement irréparable, on
tente de **retirer un mot déjà verrouillé qui croise cet emplacement** —
un mot forcément dans l'autre sens, qui participe aux lettres déjà
positionnées rendant cet emplacement difficile à remplir — plutôt que de
continuer à sur-noircir une seule zone bien au-delà de ce que l'objectif de
remplissage global prévoit. Le mot retiré est tiré au hasard parmi tous
ceux qui croisent l'emplacement, **sans aucun critère de fragilité**.
L'évaluation est répétée (une nouvelle case noire, ou un nouveau retrait de
mot) jusqu'à obtenir de nouveau au moins 3 mots compatibles pour cet
emplacement ; il n'est marqué irréparable que si ni l'un ni l'autre des
deux leviers ne débloque la situation.

### Règles structurelles

Une case n'est acceptée que si elle respecte ces règles :

- une case blanche ne peut jamais se retrouver isolée d'une seule lettre
  dans les **deux** sens à la fois (entourée de cases noires sur ses 4
  côtés) : une telle case ne ferait alors partie d'aucun mot du tout, dans
  aucune des deux directions, et ne pourrait donc jamais recevoir de
  lettre — cette règle-là reste absolue, jamais assouplie ;
- la grille blanche doit rester entièrement connectée — pas de zone blanche
  isolée du reste de la grille par un mur de cases noires ;
- un emplacement (une zone blanche encadrée par une case noire des deux
  côtés) doit normalement faire **au moins 3 cases**, **sauf** si l'une de
  ses deux extrémités touche directement le bord de la grille : dans ce
  cas, il reste toujours autorisé, quelle que soit sa longueur (y compris 1
  ou 2 cases) et quel qu'en soit le nombre sur la grille entière. Une zone
  d'une seule lettre ne sert jamais de mot à définir (juste une case de
  passage pour le mot qui la traverse dans l'autre sens) ; une zone de deux
  lettres, elle, **devient un vrai mot à deviner** avec sa propre
  définition, voir l'étape 2 ("et", "ou", "no", etc.).

L'exigence de 3 cases peut être abaissée pour une case précise (voir
"Éviter l'isolement" ci-dessous) — `minimize_black_squares` (étape 3), qui
ne fait que *retirer* des cases noires, vérifie donc la grille avec
l'exigence minimale réelle (1 case, c'est-à-dire uniquement la connexité et
l'absence de case orpheline), pas l'exigence esthétique de 3 cases, qui
n'est pas son rôle de faire respecter.

### Choisir où placer une case

Pour éviter que les cases noires se regroupent en petits paquets (ce qui
créerait des "murs" disgracieux et forcerait beaucoup de mots voisins à
avoir la même longueur), chaque nouvelle case n'est pas choisie au hasard sur
toute la grille : on tire un petit groupe de 32 positions candidates, et on
retient celle qui se trouve dans la ligne et la colonne les moins déjà
chargées en cases noires.

**Éviter l'isolement.** Parmi les 32 candidates, on cherche d'abord la
meilleure (au sens du critère ci-dessus) qui **ne touche aucune autre case
noire** et qui respecte l'exigence normale de 3 cases. Si cette exigence ne
laisse plus aucune candidate à la fois isolée et valide, elle est abaissée à
2 cases, puis à 1 case, toujours en cherchant une candidate isolée à chaque
niveau. C'est seulement si aucune candidate isolée ne fonctionne à aucun de
ces trois niveaux qu'on accepte l'adjacence — en retentant alors les mêmes
trois niveaux (3, puis 2, puis 1) sans plus exiger l'isolement. Cette
relaxation ne s'applique qu'à la case en cours de placement, pas à toute la
grille ni aux tentatives suivantes — mais elle peut légitimement laisser un
emplacement interne de 1 ou 2 cases dans la grille finale, quand c'est le
seul moyen d'éviter qu'une nouvelle case touche une case noire déjà posée.

**Exception : lors de la toute première initialisation d'une grille**
(la première grille d'une génération, avant tout palier — aucun motif reçu
d'un palier précédent), cette dernière relaxation (accepter l'adjacence en
dernier recours) est **entièrement désactivée** :
si aucune candidate isolée ne convient à aucun des 3 niveaux, la meilleure
candidate est simplement refusée et retirée du lot — la case noire n'est
donc jamais posée cette fois-ci — plutôt que d'accepter malgré tout une
case adjacente à une autre. Dans ce cas précis, la grille peut donc
légitimement finir avec moins de cases noires que l'objectif visé. Ce
comportement plus strict ne s'applique qu'à cette toute première grille —
tout palier qui reprend un motif déjà partiellement noirci d'un palier
précédent garde le comportement habituel décrit ci-dessus (adjacence
acceptée en dernier recours).

Cette même exception s'applique aussi à la phase de pré-remplissage
ci-dessus, pas seulement à ce placement par pourcentage : sur cette toute
première grille, le pré-remplissage cherche lui aussi en priorité une case
qui ne touche aucune autre case noire, sans jamais accepter l'adjacence en
dernier recours — ce qui compte particulièrement sur une grande grille, où
le pré-remplissage pose l'essentiel des cases noires (de nombreux
emplacements dépassent la longueur que le dictionnaire peut couvrir).

### Densité visée

Le pourcentage cible de cases noires visé par ce placement (`black_ratio`,
un réglage séparé réservé au CLI) est **0 % par défaut, et ne progresse pas
d'un palier à l'autre** : le pré-remplissage (au moins 3 mots candidats par
emplacement) combiné au mécanisme de reprise entre paliers (voir l'étape 3)
suffit à faire progresser la grille sans avoir à la densifier
artificiellement palier après palier. Une petite densification fixe reste
appliquée, elle, à chaque palier qui part d'une grille vierge ou d'une
simplification (jamais à un palier de reprise "telle quelle") : une fois le
pré-remplissage terminé, le pourcentage "Taux noir" décrit plus haut
transforme le nombre de cases encore blanches nécessaire en cases noires
supplémentaires.

Si le remplissage de l'étape 2 échoue malgré tout, on ne repart pas
forcément de zéro : voir l'étape 3 ("Simplifier la grille"), qui couvre
aussi bien la simplification d'une tentative bloquée que celle d'une grille
déjà réussie. Le nombre maximal de paliers avant d'abandonner est de 200.

Si les 200 paliers sont épuisés sans succès, l'interface web propose un
bouton "Continuer" : il relance 200 nouveaux paliers, mais en repartant
exactement de l'état où la tentative précédente s'est arrêtée (motif,
cases verrouillées, mots déjà confirmés) plutôt que d'une grille vierge
(`backend/crossword_gen.py`, `generate_grid` et
`_serialize_resume_state`/`_deserialize_resume_state` ; `backend/app.py`,
`POST /api/generate/continue/{job_id}`).

### Plusieurs tentatives en parallèle par palier

Puisque cette recherche est rapide et que la machine est loin de saturer son
processeur avec une seule tentative à la fois, chaque palier ne se contente
pas d'un seul essai : **autant de tentatives indépendantes en parallèle que
la machine a de processeurs, par défaut** (motif différent + remplissage
complet), chacune sur son propre processus — ce nombre est réglable dans
`env.sh` (variable `CROSSWORDFALCON_PARALLEL_ATTEMPTS`, `backend/
crossword_gen.py`) pour forcer une valeur différente du nombre de
processeurs détecté.

Tout à fait au départ (avant même le tout premier palier, donc avant tout
nettoyage), il n'existe encore aucune grille de départ commune :
**chaque tentative parallèle construit alors sa propre grille depuis zéro,
totalement indépendamment des autres** — motif noir/blanc différent
d'une tentative à l'autre, chacune tirée par son propre générateur
aléatoire (`backend/crossword_gen.py`, `make_pattern`). Ce n'est qu'à
partir du moment où un premier palier échoue que les tentatives suivantes
commencent à partager un même point de départ (motif conservé, ou grille
nettoyée) — voir plus bas.

Une fraction réglable de ces tentatives, une fois terminées — qu'elles
réussissent ou qu'elles échouent —, peut interrompre toutes les tentatives
encore en cours pour passer directement au palier suivant
(`backend/crossword_gen.py`, `generate_grid`,
`PALIER_ATTEMPT_INTERRUPT_FRACTION`, et le point de contrôle correspondant
dans `Filler._backtrack`) : le palier n'attendrait alors plus
systématiquement la tentative la plus lente du lot. Cette fraction est
pour l'instant fixée à **100 %** : le palier attend donc que **toutes** les
tentatives du lot se terminent d'elles-mêmes avant de passer à la
sélection, sans interruption anticipée. Parmi les
tentatives ainsi conclues avant l'interruption, si plusieurs réussissent,
ce n'est pas simplement la première trouvée qui est retenue : c'est celle
dont les mots ont la plus grande **somme des carrés de leurs longueurs**.
Ce score favorise quelques mots longs plutôt que beaucoup de mots courts
pour le même
total de lettres — un mot de 10 lettres pèse 100 dans ce score, alors que
dix mots de 2 lettres (qui couvrent pourtant le même nombre de lettres au
total) ne pèsent que 40.

Chaque processus worker publie, en temps réel pendant sa propre recherche,
chaque nouveau record de mots placés qu'il atteint — pas seulement l'état
auquel il s'arrête (que ce soit un échec naturel ou une interruption). Ces
publications sont envoyées au processus parent au fil de l'eau, via un
canal dédié (`backend/crossword_gen.py`, `best_state_queue`),
continuellement vidé par un processus léger dédié tournant pendant toute
la durée de la génération. Le volume de ces publications reste modeste et
borné : une tentative ne peut battre son propre record qu'une fois par mot
placé, donc au plus une cinquantaine de publications par tentative et par
palier (le nombre d'emplacements d'une grille), quel que soit le nombre
réel d'essais internes de la recherche (potentiellement des centaines de
milliers).

**Ces états publiés en temps réel ne servent qu'à l'affichage — jamais à
la vraie sélection qui décide de la base du palier suivant.** Un état
publié tôt dans une recherche encore peu avancée a mécaniquement très peu
de cases pouvant déjà être jugées injouables (peu de mots posés, donc peu
de croisements pour révéler un conflit) : le comparer à un résultat
réellement abouti d'une autre tentative fausserait la sélection. La
sélection qui alimente réellement le palier suivant ne se fie donc qu'aux
résultats finaux de vraies recherches abouties ; les états publiés par la
file restent visibles dans l'aperçu affiché à l'écran (jusqu'à 6 grilles),
dans un vivier séparé qui, lui, peut les inclure, mais qui n'a aucune
influence sur la progression réelle de la recherche. La première des
grilles montrées à l'écran est toujours, exactement, celle qui va
réellement être nettoyée et conservée pour le palier suivant si elle est
retenue — les autres grilles montrées peuvent provenir du vivier élargi
(résultats réels + états publiés par la file), mais jamais à la place de
celle-là, pour que la comparaison "avant nettoyage / après nettoyage"
entre deux aperçus successifs reste valide.

## Étape 2 — Remplir la grille avec de vrais mots

Une fois le motif de cases noires accepté, chaque suite de cases blanches
d'au moins **2** lettres (horizontale ou verticale) devient un emplacement à
remplir — un "mot à trouver", y compris les petites zones de 2 lettres
tolérées à l'étape 1 (mais jamais les zones d'1 lettre, qui restent de
simples cases de passage, sans emplacement propre). Certaines cases
appartiennent à deux emplacements à la fois (un mot horizontal et un mot
vertical qui se croisent).

Le remplissage se fait par **essais successifs avec retour en arrière**
(*backtracking*) : le programme choisit un emplacement, y place un mot du
dictionnaire qui respecte les lettres déjà posées par les mots croisés
voisins, puis passe à l'emplacement suivant. Si, à un moment, un emplacement
ne peut plus recevoir aucun mot valide (toutes les lettres déjà imposées
rendent le mot introuvable — y compris quand tous les mots qui
correspondraient encore sont déjà utilisés ailleurs dans la grille), le
programme revient en arrière, annule le dernier mot posé, et en essaie un
autre.

### Les emplacements à une seule possibilité

**Avant même le sondage statistique des graines** décrit plus bas, le
programme cherche d'abord une certitude, pas une simple tendance : pour
chaque emplacement encore jouable, si les lettres déjà connues à certaines
de ses cases ne laissent plus qu'**un seul mot du dictionnaire** possible
(éventuellement un seul mot en tout pour toute cette longueur, sans même
avoir besoin d'une seule lettre déjà connue), les lettres restantes de cet
emplacement sont directement figées sur ce mot (`backend/crossword_gen.py`,
`_force_single_candidate_slots`) — ce n'est plus une graine, un simple
indice statistique qu'un vrai mot peut encore contredire, mais un fait
acquis, puisqu'aucune autre possibilité n'existe. Fixer un tel emplacement
peut, par une case de croisement, réduire à son tour un emplacement voisin
encore incertain à une seule possibilité lui aussi — le programme
recommence donc autant de fois que nécessaire jusqu'à ce qu'un passage
complet ne trouve plus rien de nouveau à déduire. Un emplacement déjà connu
impossible n'est jamais examiné par cette étape.

### Les "graines"

**Avant même de commencer** — c'est-à-dire une fois les emplacements
entièrement déterminés ci-dessus mis de côté —, le programme se fait une
petite idée statistique de ce à quoi le reste de la grille pourrait
ressembler : pour chaque emplacement encore incertain, il tire au hasard
100 mots de la bonne longueur — uniquement
parmi les mots réellement compatibles avec les lettres déjà connues à
certaines de ses cases (héritées d'un palier précédent, voir plus bas),
s'il en existe (`backend/crossword_gen.py`, `sample_letter_biases`) — et
regarde, case par case, quelle lettre revient le plus souvent dans cet
échantillon. Une case n'est retenue comme candidate que si cette lettre
"consensuelle" est apparue plus de 10 fois sur les 100 mots — un consensus
trop faible ne garantit pas qu'il reste assez de mots compatibles une fois
la lettre figée. Parmi les cases candidates, le programme en **pioche au
hasard** un certain nombre pour en faire des **"graines"** — des
emplacements qui initient les premiers placements, ou les influencent
quand il y a déjà d'autres lettres. Le nombre de graines posées va jusqu'à
un pourcentage réglable (choisi via l'interface, 1 % par défaut) du nombre
de cases blanches **encore sans lettre connue** de la grille — pas du
nombre total de cases blanches : une case déjà connue avec certitude,
héritée d'un palier précédent, ne compte pas dans cette base de calcul,
donc le nombre de graines diminue naturellement à mesure qu'un palier de
reprise "telle quelle" confirme de plus en plus de la grille — pour la
toute première grille d'un palier, sans aucune lettre encore connue, ce
nombre correspond au total de cases blanches. Jamais plus d'une graine par
emplacement (une case qui croise
deux emplacements compte pour les deux à la fois). Ces graines ne sont que
des indices, pas des mots
réellement posés : dès qu'un véritable mot est choisi pour un emplacement
croisé, sa vraie lettre prend le pas sur l'indice. Une case déjà connue
avec certitude n'est elle-même jamais reproposée comme graine — inutile de
lui donner un indice statistique, sa lettre est déjà sue.

Une graine réduit aussi, comme une lettre réellement croisée le ferait, le
nombre de mots candidats compatibles avec son emplacement — la règle de
sélection décrite plus bas s'appuie directement sur ce nombre, donc une
graine lui donne une vraie priorité de traitement. Une graine ne doit être posée
que sur un emplacement jouable : un emplacement déjà connu impossible
(entièrement verrouillé par des lettres d'un palier précédent, mais sans
qu'aucun mot réel du dictionnaire ne corresponde à cette combinaison) ne
propose plus jamais l'une de ses cases comme graine. Ce n'est plus
seulement une règle à part : puisque le sondage lui-même ne tire déjà que
parmi les mots compatibles avec les lettres connues, un tel emplacement n'a
tout simplement **aucun mot à tirer** — le sondage y reste vide de
lui-même, sans qu'un test séparé soit nécessaire pour l'écarter.

Ce même petit sondage statistique sert aussi à autre chose, indépendamment
de savoir si une case a été réellement figée ou non : pour chaque
emplacement que le programme choisit de remplir, les mots candidats du
dictionnaire sont classés selon à quel point leurs lettres correspondent au
consensus statistique observé sur les cases pas encore déterminées par un
croisement — un mot qui colle bien au consensus sur plusieurs cases à la
fois est essayé avant un mot qui n'y colle pas du tout, plutôt qu'un tirage
entièrement aléatoire parmi tous les mots valides. Le tout premier mot
essayé n'est toutefois pas strictement le mieux classé : le programme
pioche au hasard parmi les 5000 meilleurs candidats restants à chaque fois,
une fenêtre qui se décale au fur et à mesure. Sur un emplacement encore
entièrement vierge (aucune case fixée par un croisement), ce classement
n'a en pratique plus aucun effet discriminant — une fenêtre aussi large
revient alors à un tirage quasiment uniforme dans tout le dictionnaire de
cette longueur ; la préférence pour les mots les mieux notés ne redevient
sensible qu'une fois plusieurs cases de l'emplacement déjà fixées par des
croisements.

### Choisir quel emplacement remplir en premier

Pour aller plus vite, le dictionnaire est pré-organisé pour retrouver
instantanément tous les mots d'une longueur donnée qui ont une lettre
précise à une position précise — sans cela, il faudrait relire tout le
dictionnaire à chaque tentative.

À chaque emplacement à choisir, le programme applique une règle à **deux
niveaux de priorité** :

1. on tire d'abord la **catégorie** (horizontal ou vertical) : la
   probabilité de choisir l'une ou l'autre est proportionnelle au nombre
   d'emplacements encore libres dans chacune des 2 catégories — une
   catégorie qui a encore beaucoup d'emplacements non remplis a plus de
   chances d'être tirée que l'autre. Ça fait naturellement alterner/
   équilibrer les deux catégories au fil du remplissage, sans imposer un
   ordre strict (par exemple tout l'horizontal puis tout le vertical) ;
2. à l'intérieur de la catégorie tirée, on calcule pour chaque emplacement
   le score **nombre de possibilités de remplissage** — le nombre de mots
   du dictionnaire encore compatibles avec les lettres déjà connues de cet
   emplacement (sans exclure les mots déjà utilisés ailleurs dans la
   grille) — et on tire au hasard, uniformément, **parmi les emplacements
   ayant obtenu le plus petit score** (les plus contraints, donc les plus
   urgents à résoudre), **dans une fenêtre de max(5, int(emplacements
   encore libres dans cette catégorie / 3)) emplacements** — une fenêtre
   qui s'élargit quand il reste beaucoup d'emplacements à remplir dans la
   catégorie tirée, et se resserre (jusqu'à ce plancher de 5) une fois
   qu'il n'en reste plus beaucoup.

Le programme n'essaie par ailleurs jamais de remplir un emplacement qui
croise (partage une case avec) un emplacement déjà connu comme "impossible"
(voir l'étape 3) — un mot posé là serait de toute façon retiré par le
prochain nettoyage, autant ne jamais perdre de temps à le poser. Cette règle
ne change rien tant qu'aucun emplacement n'est encore connu comme impossible
(le tout premier palier, ou tout palier qui repart d'une grille vierge ou
nettoyée) — elle n'entre en jeu que sur un palier de reprise "telle quelle".

### Limites de la recherche

Cette recherche a un budget maximal, proportionnel à la taille de la
grille par défaut (**largeur × hauteur × 2000** vérifications — 300 000 sur
la grille de référence 15×10 ; `backend/crossword_gen.py`, `try_fill`) : si
une grille s'avère trop difficile à remplir, l'étape 2 abandonne et on
passe à l'étape 3 pour simplifier la tentative avant de continuer. Depuis
l'interface web, le sélecteur **"Mode"** (Flash/Turbo/Rapide/Moyen/Ultra ;
`backend/app.py`, `BUDGET_MODES`) fixe directement ce budget par tentative
à une valeur choisie (1 000 à 5 000 000), sans rapport avec la taille de la
grille, à la place de cette formule par défaut.

Une tentative peut aussi être abandonnée bien plus tôt : dès que plus de
30 % des cases blanches de la grille appartiennent à un emplacement jugé
impossible, la tentative en cours est jugée sans espoir raisonnable et
arrêtée immédiatement, plutôt que de continuer à chercher ailleurs sur un
motif déjà aussi largement compromis (vérifié de temps en temps, toutes les
500 étapes de recherche, pas en continu ; `backend/crossword_gen.py`,
`Filler._backtrack` — voir aussi l'étape 3, "Reprise telle quelle", pour un
cas où ce signal se propage entre plusieurs tentatives parallèles).

Un emplacement n'est jugé "impossible" (ici comme dans tout le reste du
document — surlignage rouge de l'aperçu, retrait de mot/case noire à
l'étape 3, etc.) qu'à partir de lettres réellement imposées par un mot
croisé confirmé **ou déjà verrouillées d'un palier précédent** — jamais à
partir d'une simple graine statistique (`sample_letter_biases`, voir plus
haut) : une graine reste une supposition non vérifiée, qui ne redevient
plus jamais pertinente une fois la recherche arrêtée, et ne doit donc
jamais, à elle seule, faire conclure qu'un emplacement n'a aucune
solution. Les lettres verrouillées, à l'inverse, ne sont jamais ignorées
pour cette décision, même si elles n'ont jamais été redécouvertes par un
vrai mot croisé assigné *pendant cette tentative précise* : un emplacement
entièrement verrouillé d'un palier précédent, dont la combinaison ne
correspond à aucun mot réel, doit être signalé injouable exactement comme
n'importe quel autre — sinon le nettoyage entre paliers ne voit jamais de
problème à corriger, et la même combinaison invalide se reconstruit à
l'identique, cycle après cycle, sans jamais progresser ni jamais être
signalée (`backend/crossword_gen.py`, `Filler.locked_letters`, distinct de
`Filler.forced_letters`).

Un emplacement condamné dès le départ (par exemple une case noire qui coupe
un emplacement déjà partiellement verrouillé d'une façon qui ne correspond
à aucun mot du dictionnaire) est repéré juste avant que la recherche ne
commence et mis de côté comme les emplacements impossibles hérités d'un
palier précédent : la recherche continue à essayer de remplir tout le reste
de la grille au lieu de s'arrêter net à la première vérification.

### Fermeture des derniers emplacements implicites

Une fois la recherche terminée — succès, budget dépassé, abandon à 30 %,
ou interruption par une tentative sœur — un dernier passage, bon marché,
referme les emplacements qui restent formellement non assignés alors que
toutes leurs lettres sont déjà déterminées par de vrais mots croisants
réellement placés, et qu'il ne leur reste plus qu'un seul mot du
dictionnaire encore disponible (pas déjà utilisé ailleurs dans la grille) :
ce mot est confirmé directement, sans attendre que la recherche elle-même
ait eu l'occasion de le sélectionner explicitement (`backend/
crossword_gen.py`, `_close_implied_slots`, appelée depuis `try_fill`).
Contrairement aux "emplacements à une seule possibilité" (voir plus haut,
qui agit *avant* que la recherche ne commence, uniquement à partir des
lettres déjà verrouillées d'un palier précédent), celle-ci agit *après*
et tient compte des mots déjà placés pendant cette même tentative — un mot
déjà utilisé ailleurs ne peut jamais être confirmé une seconde fois, même
s'il correspond exactement aux lettres déjà en place. Répétée jusqu'à un
point fixe : refermer un emplacement peut, par une case de croisement, en
déterminer un autre à son tour.

Sans cette fermeture, une grille pouvait apparaître entièrement remplie
(chaque case blanche déjà pourvue d'une vraie lettre confirmée) et sans
aucun emplacement injouable, tout en étant encore comptée comme échouée —
il suffisait qu'un ou deux emplacements, déjà implicitement déterminés,
n'aient simplement jamais été explicitement choisis par la recherche avant
la fin de la tentative. Ce dernier pas trivial ne fait jamais progresser
la recherche elle-même (aucun mot deviné ou statistique n'est jamais
placé ici) — il ne fait que confirmer ce qui, par construction, est déjà
la seule possibilité restante.

## Étape 3 — Simplifier la grille

Cette étape intervient dans deux circonstances différentes : quand un
palier échoue (pour en récupérer ce qui reste exploitable avant de
continuer), et quand une grille est déjà entièrement remplie (pour la
densifier encore davantage). Les deux retirent du contenu plutôt que d'en
ajouter — d'où le même nom.

### Quand un palier échoue

Quand un palier échoue entièrement (aucune des tentatives parallèles n'a
donné une grille complète), avant même de choisir entre les deux cas
ci-dessous, un tout dernier recours est tenté sur la meilleure tentative
échouée de ce palier (celle qui minimise le nombre de caractères
injouables — la même grille qui sert de base aux deux cas ci-dessous) : si
tout ce qu'il reste encore sans lettre n'est rien de plus que des **cases
isolées** — des cases blanches sans lettre dont aucun des 4 voisins directs
n'est, lui non plus, sans lettre (tous ses voisins sont déjà soit noirs,
soit pourvus d'une vraie lettre) —, chacune de ces cases isolées est
bouchée d'une case noire (`backend/crossword_gen.py`,
`_plug_isolated_cells`). Une case isolée ne peut, par construction, jamais
faire partie d'un emplacement d'au moins 2 lettres encore ouvert : dès
qu'une case sans lettre a ne serait-ce qu'un seul voisin également sans
lettre, cela révèle un vrai emplacement encore à remplir quelque part, et
ce dernier recours n'y touche alors pas du tout — ni à cette case, ni à
aucune autre de la grille.

Si le résultat, une fois ces cases bouchées, reste une grille valide (pas
de case blanche orpheline créée ailleurs, grille blanche toujours connexe)
**et** que chaque emplacement de ce nouveau motif est entièrement rempli
d'un vrai mot du dictionnaire, la grille est directement déclarée
**réussie** — exactement comme un remplissage CSP qui aurait abouti
normalement, sans passer par la reprise "telle quelle" ni par le
nettoyage. Sinon (au moins une case sans lettre n'est pas isolée, ou le
résultat bouché n'est pas entièrement valide), rien n'est modifié et le
palier suit son cours normal, entre les deux cas suivants, dans cet ordre :

1. **Reprise "telle quelle".** Parmi les tentatives échouées de ce palier,
   on regarde la meilleure d'entre elles — celle qui minimise le nombre de
   caractères considérés comme injouables : s'il lui reste encore au moins
   un emplacement non rempli qui n'est ni signalé comme impossible, ni un
   emplacement qui en croise un (un tel emplacement ne sera de toute façon
   jamais tenté, donc il ne compte pas non plus comme un espoir de
   progrès), **et que moins de `MAX_CONSECUTIVE_CONTINUE_PALIERS` paliers
   "telle quelle" consécutifs se sont déjà enchaînés sans nettoyage**
   (passé ce nombre, un nettoyage est déclenché systématiquement, même si
   un espoir de progrès subsiste encore), le palier suivant repart du
   **motif rigoureusement identique**, sans régénérer aucun nouveau motif.

   **Fréquence des nettoyages complets.** Bornée par
   `MAX_CONSECUTIVE_CONTINUE_PALIERS` (valeur actuelle : 0) — un nettoyage
   peut toujours survenir plus tôt (dès que le motif courant n'a plus
   aucun espoir de progrès, voir "Simplification puis motif neuf"
   ci-dessous), mais jamais plus tard que `MAX_CONSECUTIVE_CONTINUE_
   PALIERS` paliers "telle quelle" consécutifs — ce plafond est
   systématique, même si la reprise "telle quelle" aurait encore, en
   théorie, un espoir de progrès à ce moment-là. Avec la valeur actuelle
   (0), ce plafond est déjà atteint dès le tout premier palier "telle
   quelle" candidat : **chaque palier échoué déclenche systématiquement
   un nettoyage complet**, sans jamais de reprise "telle quelle".

   Une tentative de remplissage doit aller jusqu'au bout avant qu'un
   palier ne se termine — jusqu'à ce qu'il n'y ait plus aucun emplacement
   jouable (chaque emplacement restant est soit assigné, soit signalé
   injouable), que le budget de vérifications soit dépassé, ou que la
   tentative soit interrompue par une sœur du même palier (voir
   "Plusieurs tentatives en parallèle par palier" plus bas). La décision
   "reprise telle quelle" / "nettoyage complet" n'intervient qu'*après*
   coup, une fois cette tentative réellement terminée — jamais pour
   raccourcir la tentative elle-même. Un emplacement dont les lettres déjà
   en place (par de vrais mots croisants confirmés) ne correspondent plus
   qu'à un seul mot réel et encore disponible est explicitement confirmé
   avant la fin de la tentative, même si la recherche elle-même n'a pas eu
   l'occasion de le sélectionner — voir "Fermeture des derniers
   emplacements implicites" plus bas. Si, une fois la tentative
   effectivement terminée, plus aucune case blanche n'est indéterminée et
   tous les mots en place sont valides, la grille est réputée réussie.

   Avant de transmettre le motif au palier suivant, on nettoie
   automatiquement les emplacements bloqués — mais, sauf exception (voir
   plus bas), jamais les cases noires : tout mot qui croise directement un
   emplacement impossible est retiré — les cases qu'il occupait
   redeviennent libres pour la recherche du palier suivant. Les
   emplacements déjà connus comme impossibles sont eux-mêmes mis de côté
   (ignorés plutôt que redemandés), pour laisser la recherche continuer là
   où elle s'était arrêtée plutôt que de repartir de zéro sur le même
   motif.

   **Exception : avec une probabilité d'1/10, ce retrait de mot est
   remplacé par l'ajout d'une case noire.** Uniquement sur ce chemin de
   reprise "telle quelle" — jamais sur le nettoyage complet
   ("Simplification puis motif neuf" ci-dessous), qui régénère déjà un
   motif neuf et peut donc déjà ajouter des cases noires par ce biais.
   Concrètement, avant de retirer un mot croisant un emplacement
   impossible, on tire au sort : sur un dixième des cas, on cherche plutôt
   une case de l'emplacement lui-même à noircir, en priorité une case qui
   ne porte pas déjà une lettre confirmée par un autre mot (noircir une
   case déjà confirmée détruirait ce mot-là aussi, un résultat plus
   destructeur qu'une case encore libre), mais aussi, en second recours,
   une case déjà connue si l'emplacement est entièrement croisé (le cas le
   plus fréquent en fin de partie, quand peu de cases restent réellement
   libres) — dans ce cas, la case noire retire alors, comme effet de bord,
   le mot croisant qui l'occupait. La case retenue doit garder la grille
   valide (connexe, aucune case isolée) une fois noircie ; si aucune case
   de l'emplacement (libre ou déjà connue) ne convient, on retombe sur le
   retrait de mot habituel. Poser une case noire ne libère aucune
   contrainte sur l'emplacement (contrairement au retrait de mot) : elle
   le fait disparaître purement et simplement sous sa forme actuelle, ses
   fragments réels n'étant redécouverts qu'au palier suivant, une fois le
   motif mis à jour.

   **Zone strictement sans issue : toutes ses cases restantes sont
   noircies d'un coup.** Si le retrait de mots croisants va jusqu'au bout
   (plus aucun mot croisant assigné à retirer pour cet emplacement) et
   que celui-ci n'a *toujours* strictement aucun candidat réel une fois
   toute contrainte de croisement ainsi levée — typiquement une longueur
   que le dictionnaire ne couvre pas du tout —, plus aucun retrait de mot
   ne pourra jamais débloquer cette zone : chacune de ses cases restantes
   est alors directement noircie (toujours sous réserve de garder la
   grille valide, case par case), plutôt que de laisser cette même zone
   resurgir identique à chaque nettoyage futur.

   Toutes les tentatives parallèles de ce palier partagent ce **même
   motif rigoureusement identique** — seul l'ordre dans lequel chacune
   explore les emplacements diffère. Dès que l'une d'elles est jugée
   bloquée (voir l'étape 2, "Limites de la recherche" — plus de 30 % de
   la grille jugée impossible), toutes les autres tentatives de ce même
   palier s'arrêtent aussitôt elles aussi, sans attendre d'atteindre
   individuellement leur propre seuil d'abandon ou leur propre budget
   (`backend/crossword_gen.py`, `Filler._backtrack` et `generate_grid`) —
   un motif partagé jugé bloqué par une
   tentative l'est tout autant pour les autres, inutile de les laisser
   continuer à chercher sur ce même motif. Ce signal ne vaut que pour le
   palier en cours : il est remis à zéro avant chaque nouveau palier, donc
   un blocage constaté à un palier n'affecte jamais les tentatives du
   suivant. En pratique, ce raccourci intervient rarement seul désormais :
   l'arrêt général une fois la fraction réglable de tentatives terminées
   atteinte (voir "Plusieurs tentatives en parallèle par palier" plus
   haut — pour l'instant fixée à 100 %, donc sans effet anticipé) peut,
   selon sa valeur, couper court avant même que ce signal-ci n'ait le
   temps de se propager. Ce raccourci
   ne s'applique **pas** au cas "Simplification puis
   motif neuf" ci-dessous : là, chacune des tentatives parallèles génère
   son propre motif indépendant, donc la conclusion de l'une ne dit rien
   de fiable sur celui, différent, d'une autre.
2. **Simplification puis motif neuf.** Si, au contraire, plus aucun
   emplacement non rempli n'a de chance d'aboutir (tous ceux qui restent
   sont impossibles), on simplifie la tentative en **deux temps, toujours
   dans cet ordre précis** : d'abord, on retire les mots qui croisent
   directement un emplacement impossible ; ce n'est **qu'ensuite**, une
   fois ce retrait fait, qu'on décide quelles cases noires garder — on
   rouvre (repasse en blanc) toute case noire qui ne borde plus aucun des
   mots ayant survécu au premier retrait, et on ne garde noire qu'une case
   strictement entre deux lettres confirmées, ou juste avant/après un mot
   conservé. Cet ordre compte : décider des cases noires à garder se fait à
   partir de ce qui reste *après* le retrait des mots, jamais avant.

   Ce premier retrait se fait un mot croisant à la fois, pas tous d'un
   coup : pour chaque emplacement impossible, tant qu'il reste bloqué
   (aucun vrai mot du dictionnaire ne correspond aux lettres déjà imposées
   par les mots croisants encore en place), un de ces mots croisants est
   tiré au hasard et retiré, puis on revérifie — dès qu'au moins un vrai
   mot redevient possible, l'opération s'arrête pour cet emplacement, sans
   toucher aux autres mots croisants encore là.

   Une case noire déjà présente dans le motif reçu en entrée de ce palier
   reste toujours noire, quoi qu'il arrive à son propre mot pendant la
   recherche de cette tentative précise.

   Ces deux temps sont appliqués aux tentatives échouées de ce palier
   retenues comme candidates — jusqu'à 6 (`FAILED_ATTEMPT_EXAMPLES`), au
   plus la poignée de tentatives déjà conclues au moment où les autres sont
   interrompues (voir plus haut) ; la grille conservée pour le palier
   suivant est celle qui, une fois nettoyée, maximise la **somme des
   carrés des longueurs des mots en place** (un mot n'est "en place" que
   si toutes ses cases sont confirmées — même principe que le critère qui
   choisit, plus haut, parmi plusieurs tentatives parallèles réussies),
   départagée, à score égal, par le **nombre de cases noires** de la
   candidate (la plus noire l'emporte) — sur une grille très largement
   verrouillée, cette seconde condition laisse plus de marge de manœuvre
   structurelle au palier suivant qu'un critère fondé sur les seuls mots —
   et sert de point de départ à un tout nouveau motif au palier suivant.

   Juste après un tel nettoyage complet, une des tentatives parallèles du
   palier suivant repart d'une **grille entièrement vierge** plutôt que de
   la grille nettoyée — **une seule d'entre elles** (`backend/
   crossword_gen.py`, `generate_grid`, `FULL_RESET_ATTEMPT_COUNT`) :
   toutes les autres tentatives partagent la même grille de départ (seul
   leur tirage aléatoire diffère), avec le risque qu'elles retombent
   toutes sur le même type d'impasse ; réserver une seule tentative du lot
   à un vrai nouveau départ permet parfois d'échapper à ce blocage répété,
   à moindre coût pour le contenu déjà reporté du reste du lot. Ne
   s'applique jamais après une reprise "telle quelle" (cas 1 ci-dessus),
   seulement juste après un nettoyage complet.

Aucune case noire n'est jamais ajoutée par l'un ou l'autre de ces deux cas —
seuls les mots/cases noires déjà présents dans le motif choisi survivent ou
disparaissent selon ce que le nettoyage retire.

**Un cas force systématiquement le nettoyage**, sans même regarder la
condition du cas 1 ci-dessus : si toutes les tentatives réellement conclues
de ce palier (hors celles interrompues par la fin d'une autre, voir
plus haut) ont été abandonnées tôt pour la même raison (plus de 30 % de la
grille jugée impossible, voir l'étape 2) — un signal fort qu'aucune d'elles
n'a de raison de croire qu'une reprise "telle quelle" sur son propre motif
aboutirait un jour — le nettoyage se déclenche directement, sur la
meilleure de ces grilles (`backend/crossword_gen.py`, `generate_grid`).
Avec la fraction d'interruption actuellement fixée à 100 % (voir
"Plusieurs tentatives en parallèle par palier" plus haut), toutes les
tentatives du lot ont le temps de se conclure d'elles-mêmes avant cette
vérification, donc cette règle porte sur le lot complet ; avec une
fraction plus basse, elle ne porterait que sur la poignée de tentatives
déjà conclues au moment de l'interruption anticipée.

Un emplacement est "impossible" au sens ci-dessus quand, à l'endroit où la
recherche s'est arrêtée, aucun mot du dictionnaire ne peut plus s'y placer
compte tenu des lettres déjà imposées par ses croisements — y compris quand
tous les mots qui correspondraient encore aux lettres imposées sont déjà
utilisés ailleurs dans cette même grille.

### Une fois une grille entièrement remplie

Une fois une grille entièrement remplie, on essaie d'**enlever des cases
noires** pour densifier la grille encore davantage (moins de cases noires =
plus de lettres visibles = grille plus intéressante à résoudre) : pour
chaque case noire encore présente, prise individuellement, on la retire
temporairement et on relance un remplissage complet à cet endroit. Si la
grille reste remplissable sans casser les règles structurelles de l'étape 1,
le retrait est conservé ; sinon, on remet la case noire en place et on passe
à la suivante.

Cette partie de l'étape ne peut donc **jamais dégrader** une grille déjà
valide — elle ne fait que l'améliorer quand c'est possible, jamais l'inverse.

### Aperçu affiché pendant la génération

L'interface web affiche, en direct, un aperçu de la recherche en cours
pour chaque palier : d'abord le motif de départ (le motif repris du palier
précédent, avec ses éventuelles cases/lettres verrouillées) ; puis, dès
que les cases noires de ce palier sont posées mais avant que la recherche
de mots ne démarre, le motif noir/blanc obtenu ; puis, si le palier
échoue, jusqu'à 6 des meilleures tentatives échouées avec leurs lettres
réelles et leurs diagnostics complets (cases injouables, cases
verrouillées).

Pour un palier « motif neuf » (qui pose les cases noires et lance la
recherche dans le même appel, exécuté d'un bloc dans son propre processus
worker), le motif « cases noires posées » est calculé et publié par le
processus parent lui-même, avant même de soumettre les tentatives
parallèles — en reconstruisant exactement le motif que la dernière
tentative jamais réinitialisée de ce palier va elle-même calculer dans
son propre processus (la pose des cases noires étant une fonction pure de
ses paramètres, l'appeler une seconde fois avec la même graine produit le
motif identique, au bit près). Sur le tout premier palier d'une
génération (avant que quoi que ce soit n'ait jamais été reporté d'un
palier à l'autre), ce calcul se fait une fois par tentative parallèle sur
le point d'être lancée, dédupliqué par motif réel et plafonné à 6 grilles
distinctes ; sur tout palier suivant, une seule grille suffit, puisque la
diversité existe déjà via le contenu reporté d'un palier à l'autre. Ce
mécanisme ne s'applique jamais à une reprise « telle quelle », dont
l'aperçu coïncide déjà avec le motif de départ du cycle.

Cette reconstruction, purement destinée à l'affichage, ne peut en aucun
cas altérer les lettres réellement verrouillées transmises au palier —
la pose des cases noires reçoit toujours sa propre copie indépendante des
lettres verrouillées, jamais l'objet original partagé par le reste de la
génération, précisément pour qu'une simple prévisualisation ne puisse
jamais faire disparaître, même partiellement, du contenu réellement
confirmé.

Sur ces aperçus, une case est mise en évidence par un contour rouge si
elle appartient à un emplacement au moins partiellement fixé par un
croisement réellement assigné ou par une lettre reportée d'un palier
précédent. Une case est mise en évidence par un fond orange si elle
appartient à un emplacement *partiellement* verrouillé (jamais un
emplacement entièrement verrouillé, déjà un mot confirmé) dont
l'intersection avec les lettres déjà verrouillées laisse moins de 3
candidats réels dans le dictionnaire.

## Résumé en une phrase

CrossWordFalcon place des cases noires indépendamment les unes des autres en
visant très peu de cases noires au départ, tente plusieurs fois en parallèle
(autant que de processeurs par défaut, réglable) de remplir la grille obtenue avec un vrai
dictionnaire en revenant en arrière dès qu'un emplacement se bloque ; si tout
échoue, il ne repart pas forcément de zéro — il reprend telle quelle la
meilleure tentative tant qu'elle garde un espoir de progresser, et ne la
simplifie (retirer les mots bloqués, puis rouvrir les cases noires devenues
inutiles) en vue d'un motif entièrement neuf qu'en dernier recours ; puis,
une fois une grille valide trouvée, il essaie d'en retirer encore le plus de
cases noires possible pour la rendre plus dense — sans jamais revenir sur une
grille qui fonctionne déjà.
