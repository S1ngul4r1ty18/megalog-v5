<template>
  <PageHeader
    title="Documentação"
    :subtitle="active ? `${active.title}` : 'Manuais e referências do sistema'"
  />

  <div class="p-6 grid grid-cols-1 lg:grid-cols-[260px_1fr] gap-6">
    <!-- ── Sidebar: índice ───────────────────────────────────────────── -->
    <aside class="card p-3 h-fit lg:sticky lg:top-6">
      <span class="label-micro px-2 mb-2 block">{{ docs.length }} documentos</span>
      <nav v-if="docs.length" class="space-y-0.5">
        <button
          v-for="d in docs"
          :key="d.id"
          @click="select(d.id)"
          :class="[
            'w-full text-left rounded-md px-2.5 py-1.5 text-[13px] transition-colors',
            d.id === activeId
              ? 'bg-primary-bg text-primary'
              : 'text-text-secondary hover:bg-white/[.04] hover:text-text-primary',
          ]"
        >
          <div class="font-medium">{{ d.title }}</div>
          <div class="text-text-muted text-[11px] mt-0.5 num-tabular">
            {{ formatSize(d.size_bytes) }}
          </div>
        </button>
      </nav>
      <Skeleton v-else-if="loadingList" v-for="i in 6" :key="i" height="40px" class="my-1" />
      <EmptyState v-else title="Sem documentação" subtitle="Nenhum arquivo cadastrado." />
    </aside>

    <!-- ── Conteúdo renderizado ──────────────────────────────────────── -->
    <main class="card p-6 lg:p-8 min-h-[400px]">
      <div v-if="loadingDoc" class="space-y-3">
        <Skeleton v-for="i in 8" :key="i" height="24px" />
      </div>
      <EmptyState
        v-else-if="!active"
        title="Selecione um documento"
        subtitle="Use o menu lateral pra abrir um manual."
        icon="📖"
      />
      <article v-else class="md-rendered" v-html="renderedHtml" />
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import axios from "axios";
import { marked } from "marked";
import DOMPurify from "dompurify";

import PageHeader from "@/components/PageHeader.vue";
import Skeleton from "@/components/Skeleton.vue";
import EmptyState from "@/components/EmptyState.vue";

interface DocSummary { id: string; title: string; size_bytes: number; }
interface DocContent { id: string; title: string; content: string; }

const route = useRoute();
const router = useRouter();

const docs = ref<DocSummary[]>([]);
const active = ref<DocContent | null>(null);
const loadingList = ref(true);
const loadingDoc = ref(false);

const activeId = computed(() => active.value?.id ?? "");

const renderedHtml = computed(() => {
  if (!active.value) return "";
  const raw = marked.parse(active.value.content, { async: false }) as string;
  return DOMPurify.sanitize(raw);
});

function formatSize(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

async function loadList() {
  loadingList.value = true;
  try {
    const r = await axios.get<DocSummary[]>("/api/docs");
    docs.value = r.data;
  } finally {
    loadingList.value = false;
  }
}

async function loadDoc(id: string) {
  loadingDoc.value = true;
  try {
    const r = await axios.get<DocContent>(`/api/docs/${id}`);
    active.value = r.data;
    // scroll ao topo do conteúdo ao trocar
    document.querySelector("main")?.scrollTo({ top: 0, behavior: "auto" });
  } finally {
    loadingDoc.value = false;
  }
}

function select(id: string) {
  router.replace({ query: { ...route.query, d: id } });
}

watch(
  () => route.query.d as string | undefined,
  (id) => { if (id) loadDoc(id); },
);

onMounted(async () => {
  await loadList();
  const initial = (route.query.d as string) || docs.value[0]?.id;
  if (initial) {
    if (!route.query.d) router.replace({ query: { d: initial } });
    else loadDoc(initial);
  }
});
</script>

<style>
/* ── Renderização de markdown — alinha com a paleta dark do app ─────────── */
.md-rendered {
  @apply text-text-primary leading-relaxed text-[14px];
}
.md-rendered h1 {
  @apply text-2xl font-bold mt-2 mb-4 pb-2 border-b border-border;
}
.md-rendered h2 {
  @apply text-xl font-semibold mt-7 mb-3 pb-1.5 border-b border-border;
}
.md-rendered h3 {
  @apply text-lg font-semibold mt-6 mb-2 text-text-primary;
}
.md-rendered h4 {
  @apply text-[15px] font-semibold mt-5 mb-1.5 text-text-secondary uppercase tracking-wide;
}
.md-rendered p { @apply my-3 text-text-secondary; }
.md-rendered strong { @apply text-text-primary font-semibold; }
.md-rendered em { @apply italic; }
.md-rendered a {
  @apply text-primary underline decoration-primary/40 hover:decoration-primary;
}
.md-rendered ul, .md-rendered ol {
  @apply my-3 pl-6 space-y-1 text-text-secondary;
}
.md-rendered ul { @apply list-disc; }
.md-rendered ol { @apply list-decimal; }
.md-rendered li::marker { @apply text-text-muted; }

.md-rendered code {
  @apply font-mono text-[12.5px] bg-bg-input text-primary px-1.5 py-0.5 rounded
         border border-border;
}
.md-rendered pre {
  @apply my-4 p-4 rounded-lg bg-bg-input border border-border overflow-x-auto;
}
.md-rendered pre code {
  @apply bg-transparent p-0 border-0 text-text-primary text-[12.5px];
}

.md-rendered blockquote {
  @apply my-4 pl-4 border-l-2 border-primary/40 text-text-secondary italic;
}

.md-rendered table {
  @apply my-4 w-full text-[13px] border-separate border-spacing-0;
}
.md-rendered table th {
  @apply bg-bg-elevated text-text-primary text-left px-3 py-2
         border-b border-border font-semibold;
}
.md-rendered table td {
  @apply px-3 py-2 border-b border-border text-text-secondary;
}

.md-rendered hr { @apply my-6 border-border; }

.md-rendered img {
  @apply max-w-full rounded-md border border-border my-3;
}
</style>
