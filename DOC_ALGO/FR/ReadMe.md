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
(un mot à trouver) dont la longueur a **moins de 10 mots candidats** dans le
dictionnaire — typiquement un emplacement trop long, ou d'une longueur trop
rare, pour le dictionnaire — on continue à poser des cases noires jusqu'à ce
que ce ne soit plus le cas (ou jusqu'à ce qu'il ne soit plus possible d'en
ajouter, un cas limite accepté, pas une erreur).

Les cases posées durant ce pré-remplissage comptent pour l'objectif de
pourcentage de cases noires visé (le champ "Taux noir" de l'interface,
`black_enrichment_percent`, fixé à 14 % par défaut) : ce pourcentage est
calculé une bonne fois pour toutes sur le nombre de cases blanches **avant**
le pré-remplissage, puis comparé au nombre de cases déjà posées
(pré-remplissage inclus) — si le pré-remplissage a, à lui seul, déjà posé
plus de cases que ce pourcentage n'en réclame, aucune case supplémentaire
n'est ajoutée pour cette raison ; s'il en a posé moins, seule la différence
est complétée ensuite.

Ce même pré-remplissage tourne aussi, avec un contrôle en plus, chaque fois
qu'un palier reprend un motif déjà partiellement verrouillé (voir l'étape 3) :
en plus de vérifier la longueur d'un emplacement, il vérifie alors, pour tout
emplacement touchant au moins une case déjà verrouillée par une lettre
confirmée, qu'il reste vraiment au moins 10 mots compatibles **avec ces
lettres précises à ces positions précises** — pas seulement avec sa longueur
en général. La case noire ajoutée pour corriger un tel emplacement est
choisie **directement parmi les propres cases de cet emplacement** (jamais
ailleurs dans la grille au hasard), sur la case qui touche la ligne/colonne
la moins déjà chargée en cases noires (les cases à égalité étant départagées
au hasard). Si aucune case de l'emplacement à corriger ne convient (un vrai
blocage, par exemple deux mots croisés déjà verrouillés qui ne laissent plus
aucune case libre pour le couper), cet emplacement est mis de côté comme
"impossible à corriger pour l'instant" et le pré-remplissage continue avec
les autres emplacements encore à court de candidats.

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

### Densité visée

Le pourcentage cible de cases noires visé par ce placement (`black_ratio`,
un réglage séparé réservé au CLI) est **0 % par défaut, et ne progresse pas
d'un palier à l'autre** : le pré-remplissage (au moins 10 mots candidats par
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

Dès qu'au moins **30 %** de ces tentatives se sont terminées — qu'elles
réussissent ou qu'elles échouent — toutes les tentatives encore en cours
sont interrompues, sans attendre leur propre fin, pour passer directement
au palier suivant (`backend/crossword_gen.py`, `generate_grid`, et le point
de contrôle correspondant dans `Filler._backtrack`) : le palier n'attend
donc plus systématiquement la tentative la plus lente du lot. Parmi les
tentatives ainsi conclues avant l'interruption, si plusieurs réussissent,
ce n'est pas simplement la première trouvée qui est retenue : c'est celle
dont les mots ont la plus grande **somme des carrés de leurs longueurs**.
Ce score favorise quelques mots longs plutôt que beaucoup de mots courts
pour le même
total de lettres — un mot de 10 lettres pèse 100 dans ce score, alors que
dix mots de 2 lettres (qui couvrent pourtant le même nombre de lettres au
total) ne pèsent que 40.

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
un pourcentage réglable du nombre total de cases blanches de la grille,
choisi via l'interface (1 % par défaut), mais **jamais plus d'une graine
par emplacement** (une case qui croise deux emplacements compte pour les
deux à la fois). Ces graines ne sont que des indices, pas des mots
réellement posés : dès qu'un véritable mot est choisi pour un emplacement
croisé, sa vraie lettre prend le pas sur l'indice. Une case déjà connue
avec certitude n'est elle-même jamais reproposée comme graine — inutile de
lui donner un indice statistique, sa lettre est déjà sue.

Une graine compte aussi comme une lettre déjà connue pour la règle de
sélection décrite plus bas (le score lettres remplies / sqrt(longueur)),
lui donnant une vraie priorité de traitement. Une graine ne doit être posée
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
pioche au hasard parmi les 20 meilleurs candidats restants à chaque fois,
une fenêtre qui se décale au fur et à mesure — assez pour garder une vraie
exploration plutôt qu'un choix entièrement figé à graine égale, tout en
gardant une nette préférence pour les mots les mieux notés.

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
   le score **nombre de cases encore blanches** (longueur de l'emplacement
   moins le nombre de cases déjà connues), et on tire au hasard,
   uniformément, **parmi les emplacements ayant obtenu le meilleur score,
   dans une fenêtre de max(5, int(emplacements encore libres dans cette
   catégorie / 10)) emplacements** — une fenêtre qui s'élargit quand il
   reste beaucoup d'emplacements à remplir dans la catégorie tirée, et se
   resserre (jusqu'à ce plancher de 5) une fois qu'il n'en reste plus
   beaucoup.

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
la grille de référence 15×10 ; `backend/crossword_gen.py`, `try_fill`,
ligne 1989) : si une grille s'avère trop difficile à remplir, l'étape 2
abandonne et on passe à l'étape 3 pour simplifier la tentative avant de
continuer. Depuis l'interface web, le sélecteur **"Mode"** (Flash/Turbo/
Rapide/Moyen/Ultra ; `backend/app.py`, `BUDGET_MODES`) fixe directement ce
budget par tentative à une valeur choisie (1 000 à 5 000 000), sans rapport
avec la taille de la grille, à la place de cette formule par défaut.

Une tentative peut aussi être abandonnée bien plus tôt : dès que plus de
30 % des cases blanches de la grille appartiennent à un emplacement jugé
impossible, la tentative en cours est jugée sans espoir raisonnable et
arrêtée immédiatement, plutôt que de continuer à chercher ailleurs sur un
motif déjà aussi largement compromis (vérifié de temps en temps, toutes les
500 étapes de recherche, pas en continu ; `backend/crossword_gen.py`,
`Filler._backtrack`, ligne 1582 — voir aussi l'étape 3, "Reprise telle
quelle", pour un cas où ce signal se propage entre plusieurs tentatives
parallèles).

Un emplacement condamné dès le départ (par exemple une case noire qui coupe
un emplacement déjà partiellement verrouillé d'une façon qui ne correspond
à aucun mot du dictionnaire) est repéré juste avant que la recherche ne
commence et mis de côté comme les emplacements impossibles hérités d'un
palier précédent : la recherche continue à essayer de remplir tout le reste
de la grille au lieu de s'arrêter net à la première vérification.

## Étape 3 — Simplifier la grille

Cette étape intervient dans deux circonstances différentes : quand un
palier échoue (pour en récupérer ce qui reste exploitable avant de
continuer), et quand une grille est déjà entièrement remplie (pour la
densifier encore davantage). Les deux retirent du contenu plutôt que d'en
ajouter — d'où le même nom.

### Quand un palier échoue

Quand un palier échoue entièrement (aucune des tentatives parallèles n'a
donné une grille complète), le palier suivant ne recommence pas
systématiquement avec un motif entièrement neuf. Deux cas, dans cet ordre :

1. **Reprise "telle quelle".** Parmi les tentatives échouées de ce palier,
   on regarde la meilleure d'entre elles — celle qui minimise le nombre de
   caractères considérés comme injouables : s'il lui reste encore au moins
   un emplacement non rempli qui n'est ni signalé comme impossible, ni un
   emplacement qui en croise un (un tel emplacement ne sera de toute façon
   jamais tenté, donc il ne compte pas non plus comme un espoir de
   progrès), **et que moins de 5 paliers "telle quelle" consécutifs se
   sont déjà enchaînés sans nettoyage** (passé ce nombre, un nettoyage est
   déclenché systématiquement, même si un espoir de progrès subsiste
   encore), le palier suivant repart du **motif rigoureusement identique**,
   sans régénérer ni rouvrir aucune case noire. Avant de le transmettre, on
   nettoie automatiquement les emplacements bloqués (mais jamais les cases
   noires, à une exception près — voir plus bas) : tout mot qui croise
   directement un emplacement impossible est retiré — les cases qu'il
   occupait redeviennent libres pour la recherche du palier suivant. Les
   emplacements déjà connus comme impossibles sont eux-mêmes mis de côté
   (ignorés plutôt que redemandés), pour laisser la recherche continuer là
   où elle s'était arrêtée plutôt que de repartir de zéro sur le même
   motif.

   Toutes les tentatives parallèles de ce palier partagent ce **même
   motif rigoureusement identique** — seul l'ordre dans lequel chacune
   explore les emplacements diffère. Dès que l'une d'elles est jugée
   bloquée (voir l'étape 2, "Limites de la recherche" — plus de 30 % de
   la grille jugée impossible), toutes les autres tentatives de ce même
   palier s'arrêtent aussitôt elles aussi, sans attendre d'atteindre
   individuellement leur propre seuil d'abandon ou leur propre budget
   (`backend/crossword_gen.py`, `Filler._backtrack`, lignes 1560 et 1589,
   et `generate_grid`, ligne 2998) — un motif partagé jugé bloqué par une
   tentative l'est tout autant pour les autres, inutile de les laisser
   continuer à chercher sur ce même motif. Ce signal ne vaut que pour le
   palier en cours : il est remis à zéro avant chaque nouveau palier, donc
   un blocage constaté à un palier n'affecte jamais les tentatives du
   suivant. En pratique, ce raccourci intervient rarement seul désormais :
   l'arrêt général dès 30 % de tentatives terminées (voir "Plusieurs
   tentatives en parallèle par palier" plus haut) coupe généralement court
   avant même que ce signal-ci n'ait le temps de se propager. Ce raccourci
   ne s'applique **pas** au cas "Simplification puis
   motif neuf" ci-dessous : là, chacune des tentatives parallèles génère
   son propre motif indépendant, donc la conclusion de l'une ne dit rien
   de fiable sur celui, différent, d'une autre.
2. **Simplification puis motif neuf.** Si, au contraire, plus aucun
   emplacement non rempli n'a de chance d'aboutir (tous ceux qui restent
   sont impossibles), on simplifie la tentative en **deux temps, toujours
   dans cet ordre précis** : d'abord, on retire tout mot qui croise
   directement un emplacement impossible ; ce n'est **qu'ensuite**, une
   fois ce retrait fait, qu'on décide quelles cases noires garder — on
   rouvre (repasse en blanc) toute case noire qui ne borde plus aucun des
   mots ayant survécu au premier retrait, et on ne garde noire qu'une case
   strictement entre deux lettres confirmées, ou juste avant/après un mot
   conservé. Cet ordre compte : décider des cases noires à garder se fait à
   partir de ce qui reste *après* le retrait des mots, jamais avant. Ces
   deux temps sont appliqués aux tentatives échouées de ce palier retenues
   comme candidates — jusqu'à 6 (`FAILED_ATTEMPT_EXAMPLES`), mais désormais
   au plus la poignée de tentatives déjà conclues au moment où les autres
   sont interrompues (`backend/crossword_gen.py`, `generate_grid`) — voir
   plus haut, généralement moins que le nombre total de tentatives du lot
   puisqu'une bonne partie est coupée court dès 30 % de tentatives
   terminées ; la grille
   conservée pour le palier suivant est celle qui, une fois nettoyée,
   maximise la **somme des carrés
   des longueurs des mots en place** (un mot n'est "en place" que si toutes
   ses cases sont confirmées — même principe que le critère qui choisit,
   plus haut, parmi plusieurs tentatives parallèles réussies) sert de point
   de départ à un tout nouveau motif au palier suivant.

   Juste après un tel nettoyage complet, une partie des tentatives
   parallèles du palier suivant repart d'une **grille entièrement vierge**
   plutôt que de la grille nettoyée — **20 % d'entre elles** par défaut
   (`backend/crossword_gen.py`, `generate_grid`,
   `FULL_RESET_ATTEMPT_FRACTION`) : toutes les autres tentatives partagent
   la même grille de départ (seul leur tirage aléatoire diffère), avec le
   risque qu'elles retombent toutes sur le même type d'impasse ; réserver
   une petite partie du lot à un vrai nouveau départ permet parfois
   d'échapper à ce blocage répété. Ne s'applique jamais après une reprise
   "telle quelle" (cas 1 ci-dessus), seulement juste après un nettoyage
   complet.

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
En pratique, avec l'interruption décrite plus haut dès 30 % de tentatives
terminées, il n'y a généralement plus qu'une petite poignée de tentatives
réellement conclues par palier (au lieu de la totalité du lot), donc cette
règle porte sur un échantillon plus restreint qu'auparavant.

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
