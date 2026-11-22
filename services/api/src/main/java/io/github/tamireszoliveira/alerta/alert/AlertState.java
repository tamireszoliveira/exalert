package io.github.tamireszoliveira.alerta.alert;

/** ARMED: esperando cruzar o limite. FIRED: já avisou; volta a ARMED quando o preço se afasta. */
public enum AlertState {
    ARMED,
    FIRED
}
