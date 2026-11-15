# Guideline corpus: sources and licence

`chunks.jsonl` holds short excerpts of public World Health Organization documents, each
with its source (`url`) and position (`locator`). `embeddings.json` holds one vector per
excerpt (Azure `text-embedding-3-small`, 256 dimensions). Both are produced by
`backend/scripts/build_knowledge.py`; the documents themselves are not stored here.

| Document | Link |
| --- | --- |
| Guideline: sugars intake for adults and children (2015) | https://iris.who.int/handle/10665/149782 |
| Guideline: sodium intake for adults and children (2012) | https://iris.who.int/handle/10665/77985 |
| Fact sheet: healthy diet | https://www.who.int/news-room/fact-sheets/detail/healthy-diet |
| Fact sheet: sodium reduction | https://www.who.int/news-room/fact-sheets/detail/sodium-reduction |
| EMRO: limit fat, salt and sugar intake | https://www.emro.who.int/nutrition/reduce-fat-salt-and-sugar-intake/index.html |
| #HealthyAtHome: healthy diet | https://www.who.int/campaigns/connecting-the-world-to-combat-coronavirus/healthyathome/healthyathome---healthy-diet |
| Be Healthy: healthy diet | https://www.who.int/initiatives/behealthy/healthy-diet |

These excerpts are © World Health Organization and are **not** covered by this
repository's MIT licence. WHO publications are made available under CC BY-NC-SA 3.0 IGO;
they are reproduced here unchanged, for non-commercial use, with attribution. WHO does not
endorse this project. The excerpts are split at sentence boundaries; nothing is reworded.
