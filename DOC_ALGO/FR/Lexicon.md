# Lexique — CrossWordFalcon

Ce document rassemble les définitions des termes importants employés pour
désigner les concepts de la génération de grilles (algorithme, diagnostics,
affichage). Il complète `DOC_ALGO/FR/ReadMe.md` (qui explique le
fonctionnement pas à pas) en fixant, pour chaque terme, un sens unique et
stable — pour être sûr de parler de la même chose d'une conversation à
l'autre. Comme `DOC_ALGO/FR/ReadMe.md` et `CLAUDE.md`, c'est une référence
intemporelle décrivant l'état courant, jamais un historique des décisions.

## États d'un emplacement (mot à poser) pendant la recherche

Un « emplacement » est une case blanche consécutive de la grille (une ligne
horizontale ou une colonne verticale) destinée à recevoir un mot. Pendant la
recherche, un emplacement encore vide peut se trouver dans l'un des états
suivants (non exclusifs entre eux — un même emplacement peut cumuler
plusieurs signaux) :

- **Emplacement bloqué** (aussi appelé *emplacement impossible*) : emplacement
  pour lequel il est constaté qu'aucun mot ne peut plus être posé — que ce
  soit pendant la recherche de mots elle-même (domaine vide constaté par le
  solveur), par une analyse complémentaire spécifique (recherche de case
  croisée bloquée, voir ci-dessous), ou parce que le mot qui s'y trouve
  fait dépasser un quota de qualité de contenu (trop de noms propres ou de
  mots sans définition dans la grille terminée, `MAX_PROPER_NOUNS`/
  `MAX_NON_GLOSS_WORDS`) — une grille par ailleurs entièrement et
  validement remplie peut donc quand même échouer pour ce dernier motif,
  auquel cas le(s) mot(s) responsable(s) sont signalés impossibles comme
  n'importe quel autre. Affiché en fond rouge dans les grilles
  (`frontend/static/style.css`, `.attempt-preview-grid .cell.white.impossible`).
  **Aucun mot n'est jamais posé qui croise un emplacement bloqué**, c'est-à-
  dire qui laisserait bloqué, après la pose, un des emplacements qu'il
  croise (`backend/crossword_gen.py`, `Filler._backtrack`,
  `crossing_still_impossible`). Ce refus souffre une seule exception, au
  tout dernier moment : quand tout a été tenté et que la grille est sur le
  point d'être déclarée « échouée », des mots peuvent être posés en
  travers des emplacements impossibles, de manière à en poser davantage —
  dont certains survivront peut-être au nettoyage qui suit — et à garder
  ainsi une grille mieux remplie pour l'étape suivante. En dehors de cet
  instant précis le refus reste entier, y compris dans la passe de dernier
  recours décrite ci-dessous, qui, elle, autorise seulement à CRÉER un
  emplacement bloqué. Un emplacement bloqué
  constaté pendant la recherche devient du même coup un **emplacement
  écarté** (voir ci-dessous) ; s'il redevient sain, il se croise de nouveau
  librement — un candidat est jugé sur l'état qu'il laisse, donc une pose
  dont la lettre remet un mot à portée de cet emplacement (en remplaçant
  une graine, par exemple) est parfaitement autorisée.
  (`backend/crossword_gen.py`, `Filler.impossible_zone_cells`/
  `_impossible_this_attempt`/`_impossible_indices`,
  `_quota_overflow_slot_indices`, `_interactive_fill_diagnostics`.)

- **Case croisée bloquée** : case à l'intersection de deux emplacements
  encore ouverts (un horizontal, un vertical) pour laquelle plus aucune
  lettre compatible avec les deux mots à la fois n'est disponible — chacun
  des deux emplacements peut pourtant avoir, pris isolément, un domaine non
  vide. Une case croisée bloquée provoque donc le blocage des deux
  emplacements qui s'y croisent (voir « emplacement bloqué » ci-dessus), car
  on ne pose pas un mot qui provoquerait un emplacement croisé impossible
  chez le voisin. Cette règle vaut pour les trois glossaires (Mots Défi,
  glossaire thématique, dictionnaire général) : un candidat qui rendrait
  infaisable un emplacement encore ouvert — y compris un emplacement écarté
  encore sain — est retiré aussitôt, sans descendre plus bas dans la
  recherche, et la recherche essaie le candidat suivant. Un emplacement déjà
  bloqué avant ce placement n'est jamais imputé au candidat testé : sans cette
  référence, un seul voisin déjà bloqué ferait rejeter tous les candidats de
  tous les emplacements voisins.

  Elle souffre une seule exception, en dernier recours, et cette exception
  vaut dans les deux modes : quand toutes les possibilités ont été
  explorées sur toute la grille — en génération automatique, quand la
  recherche stricte, retour en arrière jusqu'aux premiers mots posés
  compris, s'est épuisée depuis la racine (pas seulement sous une étape,
  et pas simplement faute de budget) ; en mode Interactif, en reprenant la
  recherche à trois familles sur toute la grille au niveau de tolérance
  suivant — et que la seule façon de continuer à remplir la grille est de
  poser un mot qui crée un emplacement impossible, ce mot est accepté. Mieux vaut une grille bien remplie portant une zone
  impossible, nettoyée ensuite, qu'une grille déclarée impossible très tôt
  et laissée presque vide : la zone impossible doit bien exister pour
  pouvoir être nettoyée. Les emplacements écartés sont libérés au même
  moment et pour la même raison. Cette exception-ci ne porte que sur la
  CRÉATION d'un emplacement bloqué : croiser un emplacement déjà bloqué y
  reste refusé, et n'est autorisé qu'au tout dernier moment, quand la
  grille est sur le point d'être déclarée échouée (voir « emplacement
  bloqué » ci-dessus).
  (`backend/crossword_gen.py`, `Filler.solve`,
  `Filler.breaking_permitted`, `Filler._backtrack`'s `allow_breaking` ;
  `_placement_accepted`/`PLACEMENT_LEVEL_BREAKING` pour le mode
  Interactif.)

  Affichée en rouge vif dans les grilles, plus vif que le
  simple emplacement bloqué (`frontend/static/style.css`,
  `.attempt-preview-grid .cell.white.deadlock`, couleur `--error`).
  Ce refus est réellement appliqué, pas seulement affiché : la recherche
  automatique (`Filler._backtrack`) et le pas à pas du mode Interactif
  (`_word_breaks_open_slot`, derrière le bouton **Suivant**) appellent tous
  deux la même méthode, `Filler.slot_is_blocked`, qui est la définition
  unique d'« emplacement bloqué » — plus aucun mot possible **ou** case
  croisée bloquée. Un emplacement rouge à l'écran est donc toujours vu
  comme bloqué par le code.
  (`backend/crossword_gen.py`, `Filler.slot_is_blocked`,
  `Filler._slot_letter_options`, `Filler._crossing_deadlock_slots`/
  `_crossing_deadlock_indices`, `Filler.deadlock_zone_cells`.)

- **Emplacement écarté** : emplacement qui, au moins une fois au cours de la
  recherche, n'a plus permis de poser aucun mot — soit parce que son domaine
  s'est vidé (emplacement bloqué), soit parce que tous ses candidats ont été
  refusés faute de pouvoir être posés sans créer d'emplacement impossible —
  et qui est mis de côté pour n'être retenté qu'une
  fois qu'aucun autre mot ne peut être posé ailleurs. **Constater qu'un
  emplacement est impossible à remplir en l'état provoque un retour en
  arrière** : l'étape qui le constate échoue aussitôt, puisque c'est une
  pose antérieure de la recherche qui l'a rendu impossible. Deux cas n'en
  provoquent pas, parce qu'aucun retour en arrière ne peut les réparer :
  un emplacement déjà impossible avant que la recherche ne pose quoi que ce
  soit, et un emplacement dont tous les candidats sont refusés uniquement
  parce qu'ils croiseraient un tel emplacement ; s'y ajoute, dans la passe
  de dernier recours, un emplacement rendu impossible délibérément par un
  mot de dernier recours. Le marquage « écarté », lui, reste : un
  emplacement écarté redevenu viable (il a peut-être été constaté
  impossible dans une configuration antérieure) n'est plus prioritaire,
  mais reste une possibilité de l'étape, retentée quand rien d'autre ne
  peut être posé (`backend/crossword_gen.py`, `Filler._backtrack`,
  `Filler._tolerated_dry`). 
  Remis à zéro à chaque étape (chaque tentative/palier repart sans emplacement écarté hérité tant
  qu'une nouvelle diagnose ne le reconstate pas). La liste ne garde que
  les **3 derniers emplacements écartés** (`MAX_EXCLUDED_SLOTS`) : quand un
  quatrième y entre, le plus ancien en sort ; un emplacement écarté de
  nouveau redevient le plus récent. Elle sert à détourner la recherche des
  emplacements qui viennent de poser problème, ce qui n'a plus de sens si
  presque toute la grille y figure. À chaque pose d'un mot, les
  emplacements écartés qu'il croise sont réévalués : celui qui n'est plus
  bloqué avec ce mot en place sort de la liste (`backend/crossword_gen.py`,
  `_RecentSlots`, `Filler._backtrack`). Affiché en fond jaune dans
  les grilles (`frontend/static/style.css`,
  `.attempt-preview-grid .cell.white.excluded`,
  `.cell.white.interactive-excluded`). Une case peut cumuler plusieurs
  signaux, et c'est alors le plus grave qui l'emporte visuellement : le
  jaune passe devant l'orange de l'emplacement pauvre (« plus aucun mot
  posable ici » dit davantage que « peu de candidats »), mais reste derrière
  le violet de la case croisée injouable et le rouge / rouge vif de
  l'emplacement bloqué.
  Un emplacement écarté fonctionne exactement comme tous les autres
  emplacements : son domaine est recalculé à chaque étape de la recherche, il
  est protégé comme n'importe quel autre contre un mot transverse qui le
  rendrait impossible, et il est rempli normalement dès qu'il est choisi. La
  seule différence est qu'il n'est pas envisagé pour une nouvelle pose tant
  qu'un mot peut être posé ailleurs. Il n'empêche jamais, à lui seul, de poser
  un mot qui le croise — contrairement à un emplacement bloqué (rouge), qu'on
  ne croise jamais : un emplacement écarté dont le domaine reste non vide se
  croise librement, tant que la pose ne le bloque pas. Concrètement, à chaque étape de la
  recherche :

  1. on essaie de poser un mot sur les emplacements NON écartés ;
  2. si aucun mot ne peut y être posé, les emplacements écartés sont
     *libérés* et la recherche continue, eux compris — un emplacement
     écarté qui se révèle toujours impossible à remplir provoque alors,
     lui aussi, un retour en arrière ;
  3. si rien ne peut être posé même ainsi, le backtrack normal reprend.

  La libération vaut pour toute la descente qui la suit, et se défait d'
  elle-même en remontant au-dessus de l'étape qui l'a déclenchée. Parce que
  ses cases restent toujours vides (voir « emplacement bloqué » ci-dessus),
  un emplacement écarté est toujours rendu à l'exploration : son domaine
  redevient non vide dès qu'un retour en arrière change une des lettres qui
  le croisent, et il est alors rempli comme n'importe quel autre. Un
  emplacement écarté cesse d'être affiché en jaune dès qu'un mot y est
  effectivement posé. Un emplacement écarté redevenu sain n'empêche jamais
  d'écrire des lettres autour de lui — c'est seulement la règle générale « ne
  pas poser un mot qui crée un emplacement impossible » qui lui interdit
  certains mots transverses. Un emplacement écarté peut donc être croisé par
  autant de mots que la recherche veut, du moment qu'aucune de ces poses ne
  le rend bloqué de nouveau ; tant qu'il est réellement bloqué, en revanche,
  le refus de croiser un emplacement bloqué (voir ci-dessus) s'applique à
  lui comme à n'importe quel autre.
  La liste n'a que trois sources, toutes internes à la recherche : le
  balayage préalable, le contrôle de domaine par nœud, et l'épuisement des
  candidats d'un emplacement (tous refusés parce qu'aucun ne peut être posé
  sans créer d'emplacement impossible). Aucun calcul
  d'affichage n'y écrit — un blocage croisé, en particulier, est signalé
  pour l'instantané où il est constaté, jamais mémorisé : c'est une
  propriété de l'état examiné, pas un fait durable sur l'emplacement.
  En mode Interactif, le bouton **Suivant** constate la même chose à son
  échelle : un emplacement où aucun candidat n'est acceptable est écarté pour
  ce clic — seuls les 3 derniers écartés sont gardés, comme en génération
  automatique —, renvoyé au panneau dans `excluded_cells` et affiché en fond jaune
  (`frontend/static/style.css`, `.cell.white.interactive-excluded`). La
  libération de l'étape 2 y prend la forme d'un niveau de tolérance
  supplémentaire : la recherche entière est rejouée sur toute la grille, les
  emplacements écartés au niveau précédent compris
  (`_placement_accepted`).
  (`backend/crossword_gen.py`, `Filler._impossible_this_attempt` — la liste
  elle-même, alimentée par `Filler.mark_immediately_impossible_slots`, par
  le contrôle de domaine de `Filler._backtrack` et par l'épuisement des
  candidats d'un emplacement dans cette même méthode ;
  `Filler.excluded_zone_cells` — l'affichage ; `interactive_place_word`,
  `_slot_cells_of` — l'équivalent pour le mode Interactif.)

- **Emplacement pauvre** : emplacement où l'on détecte que moins d'un
  certain nombre de mots sont possibles. Affiché en orange dans les grilles
  (`frontend/static/style.css`, `.attempt-preview-grid .cell.white.low-candidates`).
  Le seuil dépend du contexte : `PREFILL_MIN_WORD_COUNT` en mode Interactif
  et dans la cascade de sélection d'emplacement,
  `PREFILL_LOCKED_MIN_WORD_COUNT` sur la prévisualisation du motif de cases
  noires, qui ne considère que les emplacements partiellement verrouillés.
  (`backend/crossword_gen.py`, `_low_candidate_slot_cells`,
  `_interactive_fill_diagnostics`'s `low_candidate_cells`, cascade de
  sélection d'emplacement.)

- **Case croisée injouable** : case à l'intersection de deux emplacements
  pour laquelle aucune lettre n'est commune aux mots réellement *jouables*
  des deux emplacements — un mot jouable étant un candidat du dictionnaire
  ni déjà posé ailleurs dans la grille, ni en dessous du seuil de fréquence
  `NOISE_FREQUENCY_THRESHOLD`. Critère plus fin que la case croisée bloquée,
  qui, elle, raisonne sur le domaine brut : chacun des deux emplacements peut
  avoir des candidats communs, mais seulement parmi des mots trop rares ou
  déjà utilisés. Calculé uniquement sur la prévisualisation du motif de cases
  noires, pour les emplacements partiellement verrouillés. Affichée en fond
  violet dans les grilles (`frontend/static/style.css`,
  `.attempt-preview-grid .cell.white.noise`) — un signal intermédiaire entre
  l'orange de l'emplacement pauvre et le rouge de l'emplacement bloqué.
  (`backend/crossword_gen.py`, `_noise_slot_cells`.)

- **Zone d'attention** (*remplissage incrémental*) : au premier palier
  d'une recherche partie de zéro, carré des N premières lignes et N
  premières colonnes de la grille, à partir de la case (0, 0) ; seul un
  emplacement ayant une case encore libre dans ce carré peut recevoir une
  pose. N vaut d'abord 6 et grandit de 2 chaque fois que plus rien ne peut
  être posé dans la zone, jusqu'à couvrir toute la grille. Ne concerne que
  le choix de l'emplacement, jamais le retour en arrière ni la détection
  des emplacements bloqués (`backend/crossword_gen.py`,
  `INCREMENTAL_FILL_ENABLED`, `Filler._attention_pool`,
  `Filler._backtrack`). Encadrée en gras pointillé sur les aperçus
  (`frontend/static/style.css`, `.attempt-preview-grid .attention-zone`).

## Choix des mots

- **Fenêtre de tirage des mots** : la règle unique par laquelle le moteur
  décide dans quel ordre essayer les mots candidats d'un emplacement.
  Les candidats sont mélangés, puis classés par leur
  correspondance au consensus statistique des lettres sur les cases encore
  libres (`_candidate_score`), puis tirés un à un dans la fenêtre des
  **`CANDIDATE_SCORE_WINDOW` (100) meilleurs candidats restants** — qui
  se décale à mesure que les mots en sortent, jamais un simple « meilleur
  score gagne » : cette fenêtre est retriée par fréquence dans le
  dictionnaire FREQ (la plus élevée en premier, 0 pour un mot qui n'y
  figure pas) et le tirage se fait **au hasard parmi ses
  `CANDIDATE_FREQ_WINDOW` (2 × `MAX_DESCENTS_PER_NODE`, soit 20) mots les plus fréquents**. Les deux modes emploient la même méthode : la recherche
  automatique sur l'emplacement qu'elle vient de choisir, et le bouton
  **Suivant** du mode Interactif pour chacune de ses trois familles (Mots
  Défi, glossaire thématique, dictionnaire général), qui retient le premier
  candidat acceptable de cet ordre. C'est ce qui fait que deux tentatives
  parallèles — ou deux clics successifs sur un même état de grille — ne
  proposent pas forcément le même mot.
  (`backend/crossword_gen.py`, `Filler.ordered_candidates`,
  `Filler._backtrack`, `_general_dictionary_pick`,
  `_find_priority_word_placement`.)

- **Dictionnaire Scrabble** : `data/wordlist_<langue>_scrabble.tsv`, la
  liste Scrabble officielle (ou de référence) de chaque langue
  (`data/scrabble/<langue>/`) mise au format du dictionnaire de fréquences,
  sans fréquence. Versé dans le lexique de toute grille — en entier aux
  niveaux moyen et difficile, et au niveau facile seulement pour ses mots
  dont la forme accentuée figure dans la table des formes fléchies — et hors
  quotas de noms propres et de mots sans
  définition. C'est la troisième famille de l'ordre des candidats d'un
  emplacement : après les Mots Défi et le glossaire thématique, avant le
  reste du dictionnaire général. Les mots qui en sont issus s'affichent en
  lettres cyan sombre dans les aperçus et en mode Interactif.
  (`backend/crossword_gen.py`, `merge_scrabble_lexicon`,
  `Filler.scrabble_first`, `scrabble_word_cells` ; `backend/app.py`,
  `_annotate_scrabble_cells`.)

- **Case noire flottante** : case noire que le moteur a le droit de
  déplacer ou d'ajouter pour faire exister un emplacement de la longueur
  d'un Mot Défi, ou d'un mot du glossaire thématique tant que moins de 5
  mots thématiques sont posés — toute case noire qui n'est pas protégée
  (`permanent_black_cells`, les cases noires figées par l'utilisateur pour
  « Finir la grille »). (`backend/crossword_gen.py`,
  `THEME_RESHAPE_MAX_PLACED_WORDS`.)

- **Réaménagement** : modification de cases noires flottantes faite pour
  un seul mot et liée à lui. En génération automatique, c'est une option
  d'un nœud de la recherche sur l'emplacement qu'il a choisi : le nœud
  mémorise l'état d'origine des seules cases noires qu'il modifie, pose le
  mot, et remet ces cases dans leur état d'origine dès que le mot est
  refusé (sur-le-champ ou après l'échec de la suite), avant de passer à
  l'option suivante de l'emplacement. Un réaménagement ne survit donc
  jamais sans le mot pour lequel il a été fait. En mode Interactif, il n'est
  appliqué que s'il accompagne le mot réellement posé par « Suivant ».
  (`backend/crossword_gen.py`, `Filler._try_reshape`, `_undo_reshape`,
  `_find_priority_word_placement`.)

## Retour en arrière

- **Descente** : candidat qui a passé le contrôle de croisement et dans
  lequel la recherche est descendue récursivement pour remplir la suite
  de la grille. Un candidat rejeté sur-le-champ par ce contrôle n'est pas
  une descente ; un Mot Défi ou un mot du glossaire thématique n'est
  jamais compté comme une descente pour le plafond de descentes par nœud,
  sauf quand sa pose a demandé un réaménagement de case noire, qui compte
  toujours. (`backend/crossword_gen.py`, `Filler._backtrack`.)

- **Plafond de descentes par nœud** : nombre maximal de descentes
  qu'une étape de la recherche fait sans succès avant d'abandonner et de
  rendre la main à l'étape précédente, qui essaie alors son propre
  candidat suivant — tous emplacements et tous temps du nœud confondus. C'est ce qui permet au retour en arrière de
  remonter jusqu'aux mots posés dans les premières phases au lieu de
  rester à explorer le bas de l'arbre. Une valeur `<= 0` supprime le
  plafond. Un nœud qui reçoit un saut arrière n'a plus droit qu'à une
  seule descente supplémentaire ; il ne peut atteindre le plafond que si
  tous les échecs qui lui sont revenus sont des retours en arrière
  ordinaires. Un nœud atteint alors que la recherche a posé moins de
  `EARLY_DESCENTS_WORD_COUNT` (10) mots en plus de ceux de l'état initial
  de la tentative a un plafond porté à `EARLY_MAX_DESCENTS_PER_NODE`
  (2 × `MAX_DESCENTS_PER_NODE`, soit 20) descentes, le compte étant pris à
  l'entrée du nœud. Une grille héritée d'une étape précédente (tentative qui
  démarre avec des cases verrouillées) n'a aucun plafond : tous ses
  nœuds explorent toutes leurs possibilités. (`backend/crossword_gen.py`,
  `MAX_DESCENTS_PER_NODE`, `EARLY_DESCENTS_WORD_COUNT`,
  `EARLY_MAX_DESCENTS_PER_NODE`, `Filler._inherited`, `_last_jumped`.)

- **Ensemble de conflit** : ensemble des mots déjà posés dont dépend
  l'échec d'une étape de la recherche — ceux qui croisent l'emplacement
  qu'elle n'a pas pu remplir, ou qui occupent le dernier mot qu'il aurait
  pu prendre, réunis sur toutes les possibilités essayées.
  (`backend/crossword_gen.py`, `Filler._last_conflict`,
  `_dry_slot_conflict`, `_assigned_crossers`.)

- **Saut arrière** (*backjumping*) : retour en arrière qui, au lieu de
  remettre en cause le dernier mot posé, remonte directement au mot le
  plus récent de l'ensemble de conflit, en retirant au passage sans les
  remplacer tous les mots posés entre-temps qui n'y figurent pas. Un saut
  arrière retire au plus `MAX_BACKJUMP_LEVELS` (5) mots posés par la
  recherche ; au-delà, il est remplacé par un retrait fantôme (voir
  ci-dessous).
  (`backend/crossword_gen.py`, `Filler._backtrack`, `BACKJUMPING_ENABLED`,
  `MAX_BACKJUMP_LEVELS`.)

- **Emplacements candidats** : en mode Interactif, les emplacements de la
  fenêtre géométrique du niveau 6 de la cascade de choix d'emplacement —
  les `SLOT_SELECTION_WINDOW_SIZE` (10) plus proches du milieu du segment reliant le centre de la grille au centre du dernier mot posé par « Suivant » (du centre de la grille tant qu'il n'y en a pas)
  parmi ceux retenus par les niveaux précédents — dans laquelle a été
  tiré, lors d'un clic sur « Suivant », l'emplacement où le mot a été posé
  — pour le dictionnaire général, la sélection du balayage qui a fourni cet
  emplacement ; pour les Mots Défi et le glossaire thématique, la cible de
  la famille ; chaque famille évalue la cascade par rapport à son seul
  glossaire. Chacun est
  signalé par sa ou ses cases les plus proches de ce centre, entourées en
  bleu. (`backend/crossword_gen.py`, `Filler.last_selection_window`,
  `_origin_closest_cells` ; `frontend/static/script.js`,
  `interactiveWindowCells`.)

- **Retrait fantôme** (*backghost*) : simple nettoyage du conflit, qui
  remplace un saut arrière de plus de `MAX_BACKJUMP_LEVELS` (5) mots : seul
  le mot le plus récent de l'ensemble de conflit est retiré de la grille,
  sur place, sans dépiler aucun nœud ni retirer les mots posés depuis ; la
  recherche continue sur la grille ainsi libérée, et le nœud qui avait posé
  ce mot n'a plus rien à retirer quand le retour en arrière finit par
  l'atteindre. Si la reprise échoue à son tour, le même choix est refait
  sur la réunion des deux ensembles de conflit : les mots à l'origine du
  conflit sont ainsi retirés un à un, jusqu'à ce que le plus récent restant
  soit à portée d'un saut arrière. Au plus `MAX_BACKGHOSTS_PER_DESCENT` (10)
  retraits fantômes en cours sur une même descente ; au-delà, le saut
  arrière est fait en entier.
  (`backend/crossword_gen.py`, `Filler._fail_or_backghost`,
  `MAX_BACKGHOSTS_PER_DESCENT`.)

## Statistiques de lettres

- **Relevé de lettres par sens** : pour chaque case blanche, deux relevés
  distincts des lettres observées à cette case — un par emplacement qui
  la traverse (horizontal, vertical) — chacun tiré d'un échantillon de
  vrais mots compatibles avec les lettres déjà connues de son propre
  emplacement. Mis à jour, sens par sens, à chaque mot posé qui croise
  l'emplacement concerné, et défait avec la pose lors d'un retour en
  arrière. (`backend/crossword_gen.py`, `sample_letter_biases`,
  `Filler.letter_scores_by_dir`, `Filler._refresh_letter_scores_around`.)

- **Relevé croisé** : les deux relevés par sens d'une case confrontés
  l'un à l'autre — seules les lettres présentes dans les deux sens sont
  gardées, chacune avec le plus bas de ses deux décomptes. Une case
  n'appartenant qu'à un seul emplacement garde le relevé de ce seul sens.
  Le nombre de lettres du relevé croisé est le « nombre de lettres encore
  possibles » d'une case (niveau 7 du choix d'emplacement) ; sa lettre la
  plus fréquente est la **lettre statistique** de la case. C'est aussi la
  combinaison des deux sens conservée pour chaque case et relue par le
  classement des mots candidats ; rafraîchir un sens la recalcule. Un relevé
  croisé vide (aucune lettre commune) ne fait PAS de la case une case
  croisée bloquée : ce n'est qu'un échantillon, et seule la recherche, sur
  les domaines réels, en décide.
  (`backend/crossword_gen.py`, `_crossed_letter_counts`,
  `Filler._slot_min_letter_options`.)

- **Lettre statistique** : lettre la plus probable d'une case encore vide
  d'après son relevé croisé, affichée en gris clair — dans le mode
  Interactif par le bouton bistable **Stats** (activé par défaut), et dans
  les aperçus de la génération automatique (aperçus en direct et étape
  clef d'une tentative, sous le bouton **Voir**). Purement indicative :
  elle n'est jamais écrite dans la grille. (`backend/crossword_gen.py`,
  `_most_probable_letter`, `Filler.stat_letters`,
  `_interactive_letter_stats` ; `frontend/static/style.css`,
  `.interactive-stat-letter`, `.preview-stat-letter`.)

## Enchaînement des paliers

- **Nettoyage profond** : nettoyage appliqué, à la place du nettoyage
  complet ordinaire, à une grille nettoyée qui reproduit le même état
  (motif noir/blanc et contenu confirmé) deux nettoyages complets de suite.
  En plus des mots croisant un emplacement bloqué, il retire les mots qui
  croisent un mot ainsi retiré (un niveau de plus), ainsi que tout
  emplacement entièrement verrouillé dont la combinaison ne forme aucun mot
  réel. (`backend/crossword_gen.py`, `_build_retry_seed`/
  `_clean_blocked_slots` avec `deep=True`, `GRID_REPEAT_DEEP_CLEANUP_STREAK`.)

- **Nettoyage dur** (*hardclean*) : option du nettoyage des emplacements
  bloqués, activée par défaut, qui efface toutes les lettres des mots
  retirés parce qu'ils croisent un emplacement bloqué — y compris celles
  qu'ils partagent avec un mot qui ne le croise pas ; ce mot, partiellement
  effacé, est retiré entièrement à son tour, ses autres lettres effacées et
  déverrouillées si elles étaient verrouillées (sauf là où un mot entier
  restant les porte). Il efface aussi toute lettre présente sur un
  emplacement bloqué lui-même, qu'un mot la porte ou non. Une lettre
  verrouillée qu'il n'efface pas reste verrouillée, et une lettre qu'aucun
  mot entier ni aucun verrou ne porte plus (lettre orpheline) est effacée.
  Le nettoyage de fin d'étape, la seconde chance et le nettoyage dur
  précoce appliquent cette même procédure.
  Commune à la génération automatique et au bouton **Nettoyer** du mode
  Interactif. (`backend/crossword_gen.py`, `_clean_blocked_slots`,
  `HARD_CLEAN_ENABLED`.)

- **Nettoyage dur précoce** (*early hardclean*) : nettoyage dur déclenché
  plus tôt, à l'intérieur de la recherche, dès qu'un nouveau record d'une
  tentative de génération laisse au moins `EARLY_HARDCLEAN_PERCENT` (10 %)
  des cases de la grille dans des emplacements impossibles (100 =
  désactivé). Même nettoyage que la seconde chance. L'historique des
  descentes est effacé : tous les nœuds se défont, la grille est reprise à
  plat dans l'état du record, nettoyée, et le retour arrière recommence de
  zéro depuis cet état, qui devient la nouvelle racine de la recherche ; ses
  mots ne sont plus défaits que par un nettoyage dur précoce ultérieur. La
  tentative n'est ni déclarée échouée ni terminée, et ne quitte pas son
  palier.
  Il ne verrouille aucune lettre : une case verrouillée dont il efface la
  lettre est déverrouillée, une case verrouillée qu'il n'efface pas le
  reste, une lettre non verrouillée le reste, et une lettre qu'aucun mot
  complet ni aucun verrou ne porte plus (lettre orpheline) est effacée.
  (`backend/crossword_gen.py`, `Filler._early_hardclean`,
  `Filler._restart_from_record`, `Filler._early_hardclean_due`,
  `EARLY_HARDCLEAN_PERCENT`.)

- **Nettoyage dur sur mot répété** : nettoyage dur déclenché à l'intérieur
  de la recherche d'une tentative de génération quand elle pose le même mot
  au même emplacement plus de `MAX_SAME_WORD_PLACEMENTS` (100) fois de suite,
  sans autre mot posé à cet emplacement entre-temps. L'emplacement est
  déclaré impossible et nettoyé sur place, avec cet emplacement pour seul
  emplacement impossible (son mot, les mots qui le croisent et les lettres
  restées dessus sont effacés, mêmes règles de verrouillage que le nettoyage
  dur précoce). Aucun nœud n'est défait : comme pour un retrait fantôme, les
  mots retirés restent absents et un nouveau nœud poursuit la recherche ;
  l'emplacement devient un emplacement écarté et son décompte repart de
  zéro. (`backend/crossword_gen.py`, `Filler._record_tried_word`,
  `Filler._last_word_streak`, `Filler._descend`, `Filler._repeat_hardclean`,
  `MAX_SAME_WORD_PLACEMENTS`.)

- **Seconde chance** : reprise d'une tentative qui échoue alors que le
  palier est encore en course (au moins une tentative d'origine n'a pas
  atteint son budget). Au lieu d'être déclarée échouée et remplacée par une
  grille vierge, sa grille subit un nettoyage dur, sans génération de cases
  noires (motif gardé tel quel), et reprend sur le même processus. Une case
  verrouillée dont le nettoyage efface la lettre est déverrouillée. La
  tentative n'est réellement déclarée échouée que lorsqu'elle reproduit un
  état bloqué (motif et lettres placées) déjà produit au cours de ses
  secondes chances ; son processus passe alors à une grille vierge.
  (`backend/crossword_gen.py`, `generate_grid`, `_second_chance_seed`,
  `_blocked_state_key`.)

- **Grille écartée** : grille nettoyée qui reproduit une troisième fois le
  même état malgré le nettoyage profond. Elle n'est plus reprise au palier
  suivant et sa place revient à une tentative repartant d'une grille
  entièrement vierge ; les autres grilles, encore en progression, sont
  conservées. Ce n'est qu'au moment où toutes les grilles d'un palier sont
  écartées que la recherche entière repart de zéro.
  (`backend/crossword_gen.py`, `generate_grid`, `GRID_REPEAT_DISCARD_STREAK`,
  `carry_discarded_count`.)
