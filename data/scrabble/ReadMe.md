# Scrabble dictionaries

Official/reference Scrabble word lists, one folder per language, copied
unchanged from the GitHub repository
[FlandersBurger/scrabble-dictionary](https://github.com/FlandersBurger/scrabble-dictionary)
(branch `main`) by `data_builder/download_scrabble_dictionaries.py`.
`reference.json` and `LICENSE` are that repository's own files.

| Language | File(s) | Upstream source |
|---|---|---|
| fr | `ods8.txt` | ODS8 (Officiel du Scrabble), via [kamilmielnik/scrabble-dictionaries](https://github.com/kamilmielnik/scrabble-dictionaries), originally [Thecoolsim/French-Scrabble-ODS8](https://github.com/Thecoolsim/French-Scrabble-ODS8) |
| en | `sowpods.txt`, `twl.txt` | Collins Scrabble Words (SOWPODS) and NASPA Word List (TWL06), via kamilmielnik/scrabble-dictionaries, originally [wordgamedictionary.com](https://www.wordgamedictionary.com/) |
| de | `wordlist.txt` | [hippler/german-wordlist](https://github.com/hippler/german-wordlist), via kamilmielnik/scrabble-dictionaries |
| es | `file2017.txt`, `fise2.txt` | Official [FILE](https://www.filexico.com/) 2017 and [FISE](http://fisescrabble.org) 2 lexicons, via kamilmielnik/scrabble-dictionaries |
| it | `zinga.txt` | Zingarelli-based list, [scrabblewords/scrabblewords](https://github.com/scrabblewords/scrabblewords) |
| pt | `wordlist.txt` | [pythonprobr/palavras](https://github.com/pythonprobr/palavras) (LibreOffice pt_BR dictionary) |

## Licensing

The upstream repository's code is MIT-licensed (`LICENSE`). The word
lists keep their own terms: `pt/wordlist.txt` is MPL-2.0; the other lists
carry no explicit license from their upstream source — check the original
source before any commercial redistribution.

## Use in CrossWordFalcon

`data_builder/build_wordlist_scrabble.py` turns each language's lists into
`data/wordlist_<lang>_scrabble.tsv` (`MOT<TAB>ACCENTUE<TAB>CANONIQUE`, the
columns of `data/wordlist_<lang>_freq.tsv` minus the frequency). That
dictionary is merged into every grid's lexicon — whole at Medium and
Hard, only its words with a known inflection (`data/inflection/`) at
Easy; grid generation tries a slot's words of this dictionary after
the "Mots Défi" and the theme glossary, before the rest of the general
dictionary, and the words taken from it are shown in dark cyan in the
preview grids and in Interactive mode.
