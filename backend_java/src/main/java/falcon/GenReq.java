package falcon;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** GenerateRequest (backend/app.py), parsed and validated like Pydantic. */
public final class GenReq {
    public String language = "fr";
    public String bilingualLanguage;
    public int width = 15, height = 10;
    public String difficulty = "easy";
    public Long seed;
    public int forceLettersPercent = 1;
    public int blackEnrichmentPercent = 17;
    public String mode = "medium";
    public String pseudo;
    public String theme;
    public double themePrecision = Themes.THEME_MIN_SCORE;
    public String source;
    public List<String> challengeWords = new java.util.ArrayList<>();

    public static GenReq parse(Map<String, Object> json) {
        Body b = new Body(json);
        GenReq r = new GenReq();
        r.language = b.str("language", "fr");
        r.bilingualLanguage = b.str("bilingual_language", null);
        r.width = b.integer("width", falcon.gen.Generator.DEFAULT_WIDTH, 5, 30);
        r.height = b.integer("height", falcon.gen.Generator.DEFAULT_HEIGHT, 5, 30);
        r.difficulty = b.str("difficulty", "easy");
        r.seed = b.optLong("seed");
        r.forceLettersPercent = b.integer("force_letters_percent", 1, 0, 100);
        r.blackEnrichmentPercent = b.integer("black_enrichment_percent", 17, 0, 100);
        r.mode = b.str("mode", "medium");
        r.pseudo = b.str("pseudo", null);
        r.theme = b.str("theme", null);
        r.themePrecision = b.dbl("theme_precision", Themes.THEME_MIN_SCORE, 0.0, 1.0);
        r.source = b.str("source", null);
        r.challengeWords = b.strList("challenge_words");
        return r;
    }

    public Map<String, Object> dump() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("language", language);
        m.put("bilingual_language", bilingualLanguage);
        m.put("width", width);
        m.put("height", height);
        m.put("difficulty", difficulty);
        m.put("seed", seed);
        m.put("force_letters_percent", forceLettersPercent);
        m.put("black_enrichment_percent", blackEnrichmentPercent);
        m.put("mode", mode);
        m.put("pseudo", pseudo);
        m.put("theme", theme);
        m.put("theme_precision", themePrecision);
        m.put("source", source);
        m.put("challenge_words", new java.util.ArrayList<>(challengeWords));
        return m;
    }

    public boolean isBilingual() {
        return bilingualLanguage != null && !bilingualLanguage.isEmpty() && !bilingualLanguage.equals(language);
    }
}
