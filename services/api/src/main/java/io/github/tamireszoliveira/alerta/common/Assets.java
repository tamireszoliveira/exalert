package io.github.tamireszoliveira.alerta.common;

import java.util.Locale;

/** Ativos aceitos: par de moedas ("USD-BRL", câmbio na AwesomeAPI) ou ticker da B3 ("VALE3", brapi). */
public final class Assets {

    /** Mesmo critério do checker (FX_RE e STOCK_RE em alerta_checker/core.py), sem diferenciar maiúsculas. */
    public static final String PATTERN = "\\s*([A-Za-z]{3,6}-[A-Za-z]{3,6}|\\^?[A-Za-z][A-Za-z0-9.]{1,11})\\s*";

    private Assets() {
    }

    public static String normalize(String asset) {
        return asset.strip().toUpperCase(Locale.ROOT);
    }
}
