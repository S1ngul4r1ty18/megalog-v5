<template>
  <PageHeader
    title="Apoie o projeto"
    subtitle="Doação voluntária via Pix — qualquer valor ajuda a manter o sistema em evolução"
  />

  <div class="p-6 grid lg:grid-cols-2 gap-6">
    <!-- ── Coluna esquerda: explicação ─────────────────────────────────── -->
    <section class="card p-6 space-y-4 leading-relaxed text-text-secondary">
      <h2 class="text-text-primary text-lg font-semibold">Por que esse projeto existe</h2>

      <p>
        Provedores de internet — principalmente os pequenos — têm uma obrigação
        chata mas fundamental: <span class="text-text-primary">guardar o registro de quem
        usou cada conexão</span>, dia após dia, por anos. Isso vem da lei (Marco Civil
        da Internet) e quando a polícia ou a Justiça pede uma informação, é com
        esse registro que se descobre quem estava usando determinado endereço
        em determinado momento.
      </p>

      <p>
        O problema é que esse volume de dados é gigantesco. Um provedor médio
        gera <span class="text-text-primary">centenas de milhões</span> de linhas de log por dia.
        Soluções comerciais são caras e complexas; planilhas e scripts caseiros
        quebram quando o tráfego cresce. Muitos provedores acabam com sistemas
        instáveis, dados perdidos e dor de cabeça toda vez que precisam responder
        a uma requisição oficial.
      </p>

      <p>
        O <span class="text-text-primary">MegaLog</span> nasceu pra resolver isso de
        forma simples: recebe os logs do roteador, organiza por dia, comprime
        agressivamente (de gigabytes pra centenas de megabytes), permite buscar
        em segundos e mantém histórico por anos sem encher o disco. Tudo em um
        único servidor barato.
      </p>

      <p>
        É <span class="text-text-primary">software livre e gratuito</span>. Quem instalou
        e está usando não deve nada — é só usar. Mas se o sistema te ajudou a
        dormir mais tranquilo ou economizou horas de trabalho, uma contribuição
        voluntária ajuda a manter a evolução: corrigir bugs, adicionar
        funcionalidades pedidas por outros provedores e responder dúvidas.
      </p>

      <p class="text-text-muted text-[13px] pt-2 border-t border-border">
        Qualquer valor é bem-vindo. Sem assinatura, sem cobrança recorrente —
        é uma doação única quando você quiser. Obrigado por apoiar.
      </p>
    </section>

    <!-- ── Coluna direita: doação Pix ──────────────────────────────────── -->
    <section class="card p-6 space-y-5">
      <div>
        <h2 class="text-text-primary text-lg font-semibold mb-1">Doação via Pix</h2>
        <p class="text-text-secondary text-[13px]">
          Escolha um valor e escaneie o QR Code com o app do seu banco.
        </p>
      </div>

      <!-- Quick-pick -->
      <div>
        <span class="label-micro mb-2 block">Valores sugeridos</span>
        <div class="grid grid-cols-3 sm:grid-cols-6 gap-2">
          <button
            v-for="v in quickAmounts"
            :key="v"
            @click="setAmount(v)"
            :class="[
              'btn',
              amount === v ? 'bg-primary text-bg' : 'bg-white/[.04] text-text-primary border border-border hover:bg-white/[.08]',
            ]"
          >
            R$ {{ v }}
          </button>
        </div>
      </div>

      <!-- Custom -->
      <div>
        <label class="label" for="amt">Ou digite outro valor (mínimo R$ 5,00)</label>
        <div class="flex items-center gap-2">
          <span class="text-text-secondary">R$</span>
          <input
            id="amt"
            v-model.number="amount"
            type="number"
            min="5"
            step="0.01"
            class="input"
            placeholder="0,00"
          />
        </div>
        <p v-if="amountError" class="mt-2 text-[12px] text-accent-red">{{ amountError }}</p>
      </div>

      <!-- QR + copia-e-cola -->
      <div v-if="!amountError && amount >= 5" class="space-y-4 pt-2 border-t border-border">
        <div class="flex justify-center">
          <div class="bg-white p-3 rounded-lg" v-html="qrSvg" />
        </div>

        <div>
          <span class="label-micro mb-1 block">Pix Copia e Cola</span>
          <div class="flex items-stretch gap-2">
            <input
              :value="brcode"
              readonly
              class="input font-mono text-[11px] truncate"
            />
            <button @click="copy" class="btn-secondary whitespace-nowrap">
              {{ copied ? '✓ copiado' : 'Copiar' }}
            </button>
          </div>
        </div>

        <div class="text-[12px] text-text-muted space-y-1 pt-2">
          <div class="flex justify-between gap-3">
            <span>Chave Pix (aleatória)</span>
            <span class="font-mono text-text-secondary text-[11px] truncate">{{ pixKey }}</span>
          </div>
          <div class="flex justify-between">
            <span>Beneficiário</span>
            <span class="text-text-secondary">{{ displayName }}</span>
          </div>
          <div class="flex justify-between">
            <span>Valor</span>
            <span class="text-text-secondary num-tabular">R$ {{ amount.toFixed(2).replace('.', ',') }}</span>
          </div>
        </div>
      </div>
    </section>

    <!-- ── Contato (rodapé full-width) ─────────────────────────────────── -->
    <section class="lg:col-span-2 card p-5 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
      <div>
        <span class="label-micro block mb-1">Dúvidas, sugestões ou bugs?</span>
        <p class="text-text-secondary text-[13px]">
          Toda contribuição (não só financeira) é bem-vinda. Mande um e-mail
          contando como o sistema te ajudou ou o que poderia melhorar.
        </p>
      </div>
      <a
        :href="`mailto:${contactEmail}?subject=MegaLog`"
        class="btn-secondary whitespace-nowrap font-mono text-[12.5px]"
      >
        ✉ {{ contactEmail }}
      </a>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import QRCode from "qrcode";

import PageHeader from "@/components/PageHeader.vue";
import { buildPixBRCode } from "@/lib/pix";
import { useToastStore } from "@/stores/toast";

const pixKey = "44234484-ecef-412f-99bf-7a50dd073844";
// O QR usa ASCII puro (alguns bancos rejeitam acentos no campo 59).
// `displayName` é o que aparece na UI; `merchantName` é o que vai no payload.
const merchantName = "Igor de Andrade Picanco";
const displayName = "Igor de Andrade Picanço";
const merchantCity = "BRASIL";
const contactEmail = "igor.iapicanco@gmail.com";

const quickAmounts = [5, 10, 20, 50, 100, 200];
const amount = ref<number>(20);
const copied = ref(false);
const qrSvg = ref<string>("");

const toast = useToastStore();

const amountError = computed(() => {
  if (amount.value === null || isNaN(amount.value)) return "Informe um valor";
  if (amount.value < 5) return "Valor mínimo: R$ 5,00";
  if (amount.value > 100_000) return "Valor muito alto";
  return "";
});

const brcode = computed(() => {
  if (amountError.value) return "";
  return buildPixBRCode({
    key: pixKey,
    amount: Number(amount.value.toFixed(2)),
    merchantName,
    merchantCity,
  });
});

function setAmount(v: number) {
  amount.value = v;
}

async function regenerateQr() {
  if (!brcode.value) {
    qrSvg.value = "";
    return;
  }
  qrSvg.value = await QRCode.toString(brcode.value, {
    type: "svg",
    errorCorrectionLevel: "M",
    margin: 1,
    width: 240,
    color: { dark: "#000000", light: "#ffffff" },
  });
}

async function copy() {
  if (!brcode.value) return;
  try {
    await navigator.clipboard.writeText(brcode.value);
    copied.value = true;
    toast.success("Pix Copia e Cola copiado");
    setTimeout(() => (copied.value = false), 2000);
  } catch {
    toast.error("Não foi possível copiar — selecione e copie manualmente");
  }
}

watch(amount, regenerateQr);
onMounted(regenerateQr);
</script>
