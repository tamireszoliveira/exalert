package io.github.tamireszoliveira.alerta.quote;

import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Collection;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import javax.sql.DataSource;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * Cotações em SQL direto (JdbcClient): são consultas de série temporal que o JPA
 * não ajuda a escrever ({@code on conflict}, {@code distinct on}).
 *
 * <p>Instant vai como OffsetDateTime em UTC: é o tipo que o driver do PostgreSQL
 * mapeia para timestamptz.
 */
@Repository
public class QuoteRepository {

    private final JdbcClient jdbc;

    QuoteRepository(DataSource dataSource) {
        this.jdbc = JdbcClient.create(dataSource);
    }

    /** Grava as cotações; a mesma (ativo + horário da fonte) repetida é ignorada. Devolve quantas eram novas. */
    public int saveAll(List<QuoteInput> quotes, Instant receivedAt) {
        int inserted = 0;
        for (QuoteInput quote : quotes) {
            inserted += jdbc.sql("""
                    insert into quote (asset, quoted_at, price, source, received_at)
                    values (:asset, :quotedAt, :price, :source, :receivedAt)
                    on conflict (asset, quoted_at) do nothing
                    """)
                    .param("asset", quote.asset())
                    .param("quotedAt", utc(quote.quotedAt()))
                    .param("price", quote.price())
                    .param("source", quote.source())
                    .param("receivedAt", utc(receivedAt))
                    .update();
        }
        return inserted;
    }

    /** Última cotação de cada ativo pedido (os que não têm cotação ficam fora do mapa). */
    public Map<String, LatestQuote> latest(Collection<String> assets) {
        Map<String, LatestQuote> result = new HashMap<>();
        if (assets.isEmpty()) {
            return result;
        }
        jdbc.sql("""
                select distinct on (asset) asset, price, quoted_at, source
                from quote
                where asset in (:assets)
                order by asset, quoted_at desc
                """)
                .param("assets", assets)
                .query((rs, row) -> Map.entry(rs.getString("asset"), new LatestQuote(rs.getBigDecimal("price"),
                        rs.getObject("quoted_at", OffsetDateTime.class).toInstant(), rs.getString("source"))))
                .list()
                .forEach(entry -> result.put(entry.getKey(), entry.getValue()));
        return result;
    }

    public List<QuoteSeries.Point> series(String asset, Instant since) {
        return jdbc.sql("""
                select quoted_at, price
                from quote
                where asset = :asset and quoted_at >= :since
                order by quoted_at
                """)
                .param("asset", asset)
                .param("since", utc(since))
                .query((rs, row) -> new QuoteSeries.Point(rs.getObject("quoted_at", OffsetDateTime.class).toInstant(),
                        rs.getBigDecimal("price")))
                .list();
    }

    public int deleteOlderThan(Instant before) {
        return jdbc.sql("delete from quote where quoted_at < :before").param("before", utc(before)).update();
    }

    private static OffsetDateTime utc(Instant instant) {
        return instant.atOffset(ZoneOffset.UTC);
    }
}
