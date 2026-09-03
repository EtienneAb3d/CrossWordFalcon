# Comment CrossWordFalcon construit ses dictionnaires

Ce document décrit, en termes simples, comment les trois scripts à la racine
du projet — `build_sentence_corpus.py`, `build_wordlist_freq.py` et
`build_gloss_dictionary.py` — fabriquent, pour une langue donnée (français,
anglais, allemand, espagnol ou italien), les trois fichiers que
`backend/crossword_gen.py` et `backend/clues.py` utilisent ensuite pour
générer une grille et ses définitions :

1. un corpus de phrases de référence (`data/reference_corpus/<lang>_
   sentences.txt`) ;
2. une liste de mots avec fréquence, orthographe naturelle et forme racine
   (`data/wordlist_<lang>_full.tsv`) ;
3. un dictionnaire de définitions (`data/gloss_dictionary/<lang>_
   glosses.jsonl`).

Ces trois étapes s'enchaînent dans cet ordre — chacune consomme la sortie de
la précédente — mais sont indépendantes d'une langue à l'autre : rien
n'impose de traiter les cinq langues dans un ordre particulier.

## Étape 1 — Construire le corpus de phrases

(`build_sentence_corpus.py`)

### Cinq sources, cinq registres de langue

Le corpus d'une langue est construit en fusionnant cinq sources différentes,
toutes issues d'OPUS (opus.nlpl.eu) :

- **OpenSubtitles** — sous-titres de films/séries : dialogue familier,
  vocabulaire du quotidien (verbes conjugués, noms courants) ;
- **Wikipedia** — texte encyclopédique : vocabulaire formel/technique, et
  des mots rares mais réels (comme le français "are", l'unité de surface)
  que le dialogue n'emploie presque jamais ;
- **Books** — prose littéraire (romans traduits, pour l'essentiel anciens) :
  un troisième registre, narratif/descriptif, plus riche que le dialogue ou
  le texte encyclopédique ;
- **TED2013** — transcriptions de conférences TED : un quatrième registre,
  oral mais préparé et explicatif — un discours à la première personne
  adressé à un large public, différent à la fois des courts échanges
  d'OpenSubtitles et de la prose encyclopédique de Wikipedia ;
- **CCMatrix** — bitexte à grande échelle extrait de CommonCrawl : un
  cinquième registre, celui du web contemporain écrit au quotidien (articles
  d'actualité, blogs, descriptions de produits/services, forums), couvrant
  un éventail de sujets et de vocabulaire bien plus large que les quatre
  autres sources réunies.

Chacune de ces cinq sources comble un manque réel des quatre autres.

### Téléchargement partiel

Le fichier complet d'une source, pour une langue, pèse plusieurs gigaoctets
compressés — et jusqu'à 10-37 Go pour CCMatrix seule, dont l'échelle
CommonCrawl dépasse largement celle des quatre autres sources. Télécharger
l'intégralité de chaque source pour chaque langue serait impraticable :
seuls les premiers `--max-bytes` (1 Go par défaut, réglable) du fichier
compressé sont donc récupérés, via une requête HTTP "Range" ; le flux gzip
ainsi obtenu, nécessairement tronqué, est décompressé jusqu'au point où il
devient invalide, et tout ce qui a été décodé avant ce point est conservé —
le reste, tronqué, est simplement abandonné (`build_sentence_corpus.py`,
`_download_partial`/`_decompress_partial`). Cela suffit à obtenir plusieurs
centaines de milliers de lignes candidates par source, quelle que soit la
taille réelle du fichier complet.

Les phrases brutes de chaque source (avant tout filtrage) sont mises en
cache dans `CORPUS/` (racine du projet, ignoré par git), un fichier par
langue et par source — une source déjà présente en cache est relue depuis
le disque plutôt que retéléchargée, pour qu'un retraitement ultérieur
(nouvelle stratégie de filtrage, source ajoutée, notation modifiée) n'ait
pas besoin de retélécharger des centaines de milliers de lignes à chaque
fois (`build_sentence_corpus.py`, `_fetch_source_sentences`). C'est un cache
brut, par source, distinct du fichier final fusionné/filtré
`data/reference_corpus/<lang>_sentences.txt` que le reste du pipeline lit
réellement.

### Filtrage par longueur

Une phrase n'est retenue que si elle compte entre `MIN_WORDS_PER_SENTENCE`
(5) et `MAX_WORDS_PER_SENTENCE` (50) mots (`build_sentence_corpus.py`,
`_split_sentences`). Le seuil bas écarte les fragments trop courts pour
porter un vrai sens ("Oui.", "Ça va ?", un simple prénom) — ni un exemple
d'usage utile pour `backend/clues.py`, ni une donnée de fréquence
significative pour l'étape 2 ; le seuil haut écarte les blocs de texte trop
longs ou visiblement fusionnés (plusieurs phrases collées).

### Filtrage par langue

Aucune des cinq sources n'est parfaitement monolingue par fichier de
langue : du texte d'une autre langue s'y glisse par endroits (dialogue,
citation, article). Chaque phrase candidate est donc tokenisée et comparée
au dictionnaire Hunspell de la langue visée (le même mécanisme, et le même
cache `data/hunspell_cache/`, que l'étape 2 — voir plus bas) ; une phrase
est écartée dès que l'une de ces deux conditions est vraie
(`build_sentence_corpus.py`, `_filter_by_language`) :

- **`MAX_INVALID_RUN` (3)** — une suite d'au moins 3 mots consécutifs non
  reconnus par le dictionnaire, presque toujours le signe d'une phrase ou
  d'une citation entière dans une autre langue ;
- **`MAX_INVALID_WORD_FRACTION` (0,25)** — plus d'un quart des mots de la
  phrase non reconnus, même sans qu'aucune suite de 3 ne soit atteinte
  (une phrase courte, étrangère de bout en bout, avec des mots reconnus et
  non reconnus qui alternent).

Ces deux seuils ont été calés à la main sur des exemples réels de
contamination (du dialogue anglais qui s'invite dans un fichier d'une autre
langue) par rapport à des phrases authentiques ne comportant qu'un ou deux
noms propres étrangers.

### Résultat

Les phrases retenues, toutes sources confondues et déjà filtrées par
longueur puis par langue, sont écrites dans
`data/reference_corpus/<lang>_sentences.txt` (`build_sentence_corpus.py`,
`build_sentence_corpus`) — un fichier texte, une phrase par ligne.

## Étape 2 — Construire la liste de mots avec fréquence

(`build_wordlist_freq.py`)

Cette étape lit le corpus de l'étape précédente et produit
`data/wordlist_<lang>_full.tsv`, un fichier à quatre colonnes séparées par
des tabulations : `MOT<TAB>ACCENTUE<TAB>FREQUENCE<TAB>CANONIQUE`.

### Comptage des occurrences

La fréquence de chaque mot est le nombre de fois où il apparaît dans le
corpus de l'étape 1, compté directement à cette étape (pas de fichier
intermédiaire séparé) — insensible à la casse, accents conservés
(`build_wordlist_freq.py`, `_count_word_frequencies`).

### Les quatre colonnes

- **MOT** — la forme du mot telle qu'elle apparaît dans la grille : accents
  et diacritiques retirés, tout en majuscules.
- **ACCENTUE** — l'orthographe naturelle telle qu'écrite dans le corpus
  (accents et casse d'origine conservés) — transmise à `backend/clues.py`
  pour que le modèle de langage voie le genre, le nombre et la conjugaison
  réels du mot, que la forme brute (MOT) ne conserve pas.
- **FREQUENCE** — voir "Un score corrigé" ci-dessous.
- **CANONIQUE** — voir "Forme(s) canonique(s)" ci-dessous.

Un mot de moins de 2 lettres (une fois les accents retirés) est exclu — un
emplacement de grille à 2 cases est un vrai mot jouable ("et", "ou", "no"...
— voir `DOC_ALGO/FR/ReadMe.md`), mais un emplacement à 1 case n'est jamais
un vrai emplacement, donc jamais recherché dans ce fichier
(`build_wordlist_freq.py`, `main`).

### Une seconde validation dictionnaire, indépendante

Le filtrage par langue de l'étape 1 porte sur la phrase entière, pas sur
chaque mot individuellement : chaque candidat de cette étape est donc
revalidé un par un, séparément, contre le dictionnaire Hunspell de sa
langue (`.dic`/`.aff`, téléchargés depuis LibreOffice/dictionaries et mis
en cache dans `data/hunspell_cache/`), avec l'outil en ligne de commande
`hunspell` lui-même plutôt qu'avec `unmunch` (qui pré-génère toutes les
formes d'un dictionnaire à l'avance) — `unmunch` s'est montré incapable de
produire de nombreuses conjugaisons irrégulières (français "être"/
"avoir"/"vouloir" : SUIS, ÉTAIT, VEUX, SONT...) que le correcteur `hunspell`
lui-même reconnaît correctement (`build_wordlist_freq.py`,
`_spellcheck_valid`).

Le mot est vérifié à la fois tel quel et sous une forme avec initiale
majuscule, car certaines langues (l'allemand) exigent une majuscule sur
chaque nom commun alors que le corpus est en minuscules — la forme qui
valide réellement devient la colonne ACCENTUE (un mot allemand comme
"haus" devient ainsi "Haus", jamais gardé tel quel). Un candidat qui ne
valide sous aucune des deux formes est définitivement écarté.

### Doublons

Quand un même MOT (après retrait des accents) provient de plusieurs formes
accentuées distinctes, seule celle à la fréquence la plus élevée est
conservée (`build_wordlist_freq.py`, `main`).

### Un score corrigé

Un corpus dominé par le dialogue sous-représente certaines formes fléchies
pourtant courantes et faciles (le français "déterminées" y est rare, alors
que son infinitif "déterminer" est très courant). Pour corriger ce biais,
l'analyse morphologique de Hunspell (`hunspell -m`) associe à chaque mot sa
ou ses forme(s) canonique(s) (son radical — un mot peut être authentiquement
ambigu entre plusieurs radicaux, par exemple le français "suis", qui
remonte à la fois à "être" et à "suivre") ; le score final FREQUENCE mélange
`CANONICAL_WEIGHT` (90 %) de la fréquence du radical candidat le plus
fréquent avec 10 % de la fréquence brute du mot lui-même — assez pour
corriger la distorsion du corpus, tout en laissant deux formes fléchies
d'un même radical se classer différemment l'une de l'autre plutôt que de
recevoir exactement le même score (`build_wordlist_freq.py`,
`CANONICAL_WEIGHT`).

### Forme(s) canonique(s)

La colonne CANONIQUE conserve, elle, **toutes** les formes candidates
trouvées par l'analyse morphologique — pas seulement celle utilisée pour le
score ci-dessus — séparées par un point-virgule quand il y en a plusieurs
(par exemple "être;suivre" pour "suis"). Un dictionnaire de définitions est
indexé par lemme, pas par forme fléchie : cette colonne est donc ce qui
permet à `backend/gloss_lookup.py` de retrouver une définition. Garder
toutes les formes plutôt que la seule la plus fréquente laisse une
ambiguïté réelle ouverte jusqu'au moment où le modèle de langage rédige
effectivement la définition, dans le contexte du mot précis à définir,
plutôt que de la trancher arbitrairement dès la construction du dictionnaire
(`build_wordlist_freq.py`, `_stem_map`).

### Noms propres probables

Un mot dont seule la forme à majuscule initiale a validé Hunspell — alors
qu'il n'était pas déjà en majuscule dans le corpus — est presque
certainement un nom propre (personne, lieu, marque) qui apparaissait en
minuscule dans une phrase du corpus. Son FREQUENCE final est alors multiplié
par `PROPER_NOUN_SCORE_FACTOR` (0,5) : pas une exclusion pure, un nom propre
réellement très fréquent peut toujours figurer parmi les mots faciles, mais
un nom propre rare est repoussé plus loin dans le classement
(`build_wordlist_freq.py`, `PROPER_NOUN_SCORE_FACTOR`). Cette règle ne
s'applique jamais à l'allemand (`PROPER_NOUN_LANGS`) : chaque nom commun
allemand exige lui aussi une majuscule initiale, donc "n'a validé qu'avec
une majuscule" n'apporte aucune information sur le caractère propre ou
commun du mot dans cette langue précise — appliquer la règle là aussi
pénaliserait des noms communs ordinaires ("Haus") aussi souvent que de
vrais noms propres.

## Étape 3 — Construire le dictionnaire de définitions

(`build_gloss_dictionary.py`)

Cette étape télécharge l'extraction Wiktionary de Kaikki.org (kaikki.org,
lui-même dérivé des dumps Wiktionary, sous licence CC-BY-SA/GFDL comme
Wiktionary) et n'en garde que les définitions des lemmes réellement utilisés
par la colonne CANONIQUE de `data/wordlist_<lang>_full.tsv` de l'étape 2.

### Une édition Wiktionary par langue

Pour l'anglais, l'extraction principale de Kaikki (regroupée par langue du
mot défini) est déjà en anglais, puisque le Wiktionary anglophone définit
les mots anglais en anglais. Pour le français, l'allemand, l'espagnol et
l'italien, cette même extraction principale donne des définitions
**en anglais** (le regard du Wiktionary anglophone sur un mot étranger) —
ce n'est pas ce qu'il faut ici, donc ces quatre langues utilisent chacune
leur propre édition Wiktionary dans sa propre langue (frwiktionary,
dewiktionary, eswiktionary, itwiktionary), qui donne des définitions
rédigées dans la langue même du mot (`build_gloss_dictionary.py`,
`KAIKKI_SOURCE`).

### Téléchargement complet, mise en cache

Contrairement aux sources de l'étape 1, ces fichiers ne peuvent pas être
utilement téléchargés partiellement : ils ne sont pas triés par fréquence,
donc un téléchargement partiel ne couvrirait que les mots commençant par
les toutes premières lettres de l'alphabet. Chaque édition est donc
téléchargée intégralement (plusieurs gigaoctets) et mise en cache dans
`DICS/` (racine du projet, ignoré par git) — un dump déjà présent en cache
est relu depuis le disque plutôt que retéléchargé, pour qu'un retraitement
ultérieur (la colonne CANONIQUE a changé, `MAX_GLOSSES_PER_WORD` a changé)
n'ait pas besoin de retélécharger plusieurs gigaoctets par langue à chaque
fois (`build_gloss_dictionary.py`, `build_gloss_dictionary`). C'est un
cache brut du dump complet, distinct du fichier final, filtré et réellement
utilisé par l'application, `data/gloss_dictionary/<lang>_glosses.jsonl`.

### Filtrage et format de sortie

Seules les entrées dont le mot défini correspond (insensible à la casse) à
l'un des lemmes recherchés sont conservées, jusqu'à `MAX_GLOSSES_PER_WORD`
(3) définitions par mot (`build_gloss_dictionary.py`,
`build_gloss_dictionary`). Le résultat est écrit au format JSON Lines, une
ligne par lemme trouvé :

```json
{"word": "...", "entries": [{"pos": "...", "glosses": ["...", "..."]}]}
```

`backend/gloss_lookup.py` lit ensuite ce fichier pour retrouver, au moment
de rédiger une définition (`backend/clues.py`), une vraie définition d'un
mot de la grille par sa forme canonique.

## Ordre et dépendances entre les trois étapes

Les trois scripts s'enchaînent dans l'ordre décrit ci-dessus, chacun lisant
la sortie du précédent : `build_wordlist_freq.py` a besoin du corpus de
`build_sentence_corpus.py`, et `build_gloss_dictionary.py` a besoin de la
colonne CANONIQUE produite par `build_wordlist_freq.py`. Un changement dans
l'une des sources ou des règles de l'étape 1 ou 2 peut donc modifier quels
mots/lemmes existent en aval — les trois étapes sont recalculées ensemble à
chaque changement de ce genre, jamais seulement la première.

## Résumé en une phrase

CrossWordFalcon fabrique, pour chaque langue, un corpus de phrases réelles
tiré de cinq registres d'écriture différents, en compte les mots pour en
tirer une liste triée par fréquence — corrigée par la forme canonique de
chaque mot et par la détection des noms propres probables — puis va
chercher, pour chaque forme canonique de cette liste, une vraie définition
dans Wiktionary ; le générateur de grille (`backend/crossword_gen.py`) et le
rédacteur de définitions (`backend/clues.py`) n'utilisent ensuite plus que
ces trois fichiers, jamais les sources brutes elles-mêmes.
