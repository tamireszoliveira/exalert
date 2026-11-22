package io.github.tamireszoliveira.alerta.event;

public enum EventType {
    /** Alerta cruzou o limite (vira push). */
    FIRED,
    /** Preço se afastou do limite: alerta volta a valer (só histórico). */
    REARMED,
    /** Fonte de cotação falhando há muito tempo (vira push). */
    SOURCE_DOWN,
    /** Fonte voltou depois de um aviso de falha (vira push). */
    SOURCE_UP
}
