package falcon;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** Typed, validated access to a JSON request body (the Pydantic models of
 * backend/app.py): missing optional fields take their default, a wrong type
 * or an out-of-range value raises the same 422 FastAPI would. */
public final class Body {
    private final Map<String, Object> m;

    public Body(Map<String, Object> m) {
        this.m = m;
    }

    public boolean has(String k) {
        return m.containsKey(k) && m.get(k) != null;
    }

    public Object raw(String k) {
        return m.get(k);
    }

    public String str(String k, String def) {
        Object v = m.get(k);
        if (v == null) return m.containsKey(k) ? null : def;
        if (v instanceof String s) return s;
        throw Web.validation("body", k, "string_type", "Input should be a valid string", v);
    }

    public String required(String k) {
        if (!m.containsKey(k)) throw Web.validation("body", k, "missing", "Field required", null);
        Object v = m.get(k);
        if (!(v instanceof String s)) throw Web.validation("body", k, "string_type", "Input should be a valid string", v);
        return s;
    }

    public String requiredMin1(String k) {
        String s = required(k);
        if (s.isEmpty()) throw Web.validation("body", k, "string_too_short", "String should have at least 1 character", s);
        return s;
    }

    public Long optLong(String k) {
        Object v = m.get(k);
        if (v == null) return null;
        if (v instanceof Number n && n.doubleValue() == Math.rint(n.doubleValue())) return n.longValue();
        throw Web.validation("body", k, "int_type", "Input should be a valid integer", v);
    }

    public int integer(String k, int def, Integer ge, Integer le) {
        Object v = m.get(k);
        int out;
        if (v == null) {
            if (m.containsKey(k)) throw Web.validation("body", k, "int_type", "Input should be a valid integer", null);
            return def;
        }
        if (v instanceof Number n && n.doubleValue() == Math.rint(n.doubleValue())) {
            out = n.intValue();
        } else if (v instanceof String s) {
            try {
                out = Integer.parseInt(s.trim());
            } catch (NumberFormatException e) {
                throw Web.validation("body", k, "int_parsing", "Input should be a valid integer, unable to parse string as an integer", v);
            }
        } else {
            throw Web.validation("body", k, "int_type", "Input should be a valid integer", v);
        }
        if (ge != null && out < ge) throw Web.validation("body", k, "greater_than_equal", "Input should be greater than or equal to " + ge, v);
        if (le != null && out > le) throw Web.validation("body", k, "less_than_equal", "Input should be less than or equal to " + le, v);
        return out;
    }

    public double dbl(String k, double def, Double ge, Double le) {
        Object v = m.get(k);
        double out;
        if (v == null) {
            if (m.containsKey(k)) throw Web.validation("body", k, "float_type", "Input should be a valid number", null);
            return def;
        }
        if (v instanceof Number n) out = n.doubleValue();
        else if (v instanceof String s) {
            try {
                out = Double.parseDouble(s.trim());
            } catch (NumberFormatException e) {
                throw Web.validation("body", k, "float_parsing", "Input should be a valid number, unable to parse string as a number", v);
            }
        } else throw Web.validation("body", k, "float_type", "Input should be a valid number", v);
        if (ge != null && out < ge) throw Web.validation("body", k, "greater_than_equal", "Input should be greater than or equal to " + ge, v);
        if (le != null && out > le) throw Web.validation("body", k, "less_than_equal", "Input should be less than or equal to " + le, v);
        return out;
    }

    public boolean bool(String k, boolean def) {
        Object v = m.get(k);
        if (v == null) return def;
        if (v instanceof Boolean b) return b;
        throw Web.validation("body", k, "bool_type", "Input should be a valid boolean", v);
    }

    public List<String> strList(String k) {
        Object v = m.get(k);
        List<String> out = new ArrayList<>();
        if (v == null) return out;
        if (!(v instanceof List<?> l)) throw Web.validation("body", k, "list_type", "Input should be a valid list", v);
        for (Object o : l) {
            if (!(o instanceof String s)) throw Web.validation("body", k, "string_type", "Input should be a valid string", o);
            out.add(s);
        }
        return out;
    }

    public List<Object> list(String k, boolean required) {
        Object v = m.get(k);
        if (v == null) {
            if (required) throw Web.validation("body", k, "missing", "Field required", null);
            return new ArrayList<>();
        }
        if (!(v instanceof List<?>)) throw Web.validation("body", k, "list_type", "Input should be a valid list", v);
        return Json.asList(v);
    }

    /** list[list[str]] (grids). */
    public List<Object> grid(String k) {
        List<Object> rows = list(k, true);
        for (Object row : rows) {
            if (!(row instanceof List<?> l)) throw Web.validation("body", k, "list_type", "Input should be a valid list", row);
            for (Object c : l) if (!(c instanceof String)) throw Web.validation("body", k, "string_type", "Input should be a valid string", c);
        }
        return rows;
    }

    /** list[list[int]] (cells). */
    public List<int[]> cells(String k, boolean required) {
        List<Object> rows = list(k, required);
        List<int[]> out = new ArrayList<>();
        for (Object row : rows) {
            if (!(row instanceof List<?> l)) throw Web.validation("body", k, "list_type", "Input should be a valid list", row);
            int[] a = new int[l.size()];
            for (int i = 0; i < l.size(); i++) {
                if (!(l.get(i) instanceof Number n)) throw Web.validation("body", k, "int_type", "Input should be a valid integer", l.get(i));
                a[i] = n.intValue();
            }
            out.add(a);
        }
        return out;
    }

    public Map<String, Object> dict(String k) {
        Object v = m.get(k);
        if (v == null) return null;
        if (!(v instanceof Map<?, ?>)) throw Web.validation("body", k, "dict_type", "Input should be a valid dictionary", v);
        return Json.asMap(v);
    }
}
