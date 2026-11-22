package io.github.tamireszoliveira.alerta.alert;

import java.math.BigDecimal;

import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Digits;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

import io.github.tamireszoliveira.alerta.common.Assets;

/** Corpo de POST e PUT em /api/alerts. */
public record AlertRequest(
        @NotBlank(message = "informe o ativo (ex.: USD-BRL ou VALE3)")
        @Pattern(regexp = Assets.PATTERN, message = "use um par de moedas (USD-BRL) ou um ticker da B3 (VALE3)")
        String asset,

        @Size(max = 60, message = "nome com até 60 caracteres")
        String name,

        @NotNull(message = "informe se avisa abaixo (BELOW) ou acima (ABOVE)")
        Direction direction,

        @NotNull(message = "informe o limite")
        @DecimalMin(value = "0", inclusive = false, message = "o limite deve ser maior que zero")
        @Digits(integer = 12, fraction = 6, message = "limite com até 6 casas decimais")
        BigDecimal threshold,

        @DecimalMin(value = "0", message = "rearme entre 0% e 50%")
        @DecimalMax(value = "50", message = "rearme entre 0% e 50%")
        @Digits(integer = 2, fraction = 3, message = "rearme com até 3 casas decimais")
        BigDecimal rearmPct,

        Boolean enabled) {
}
