# Étape 1 — Exploration des données

**Échantillon analysé :** 2026-08-01, 15h00 UTC
**Date de l'analyse :** 2026-08-11
**Auteur :** Khaled Allouch

---

## 1. Volume

| Mesure | Valeur |
|---|---|
| Événements sur 1 heure | 168 788 |
| Taille compressée | 20,9 Mo |
| Taille décompressée | 104,3 Mo |
| Ratio de compression | 5,0× |
| Poids moyen par événement | 618 octets |

**Projection sur 6 mois (~4 380 heures) :**

- ~740 millions d'événements
- **~92 Go** de stockage Bronze (compressé, tel que téléchargé)
- ~460 Go à traiter en décompressé

**Décision :** le backfill de 6 mois tient sur un disque local. Pas besoin de
cloud payant pour la couche Bronze. En Silver, on passera en Parquet, qui
compresse mieux que gzip sur des données colonnaires.

---

## 2. Répartition des types d'événements

| Type | Volume | Part |
|---|---|---|
| PushEvent | 160 767 | **95,2 %** |
| CreateEvent | 5 425 | 3,2 % |
| DeleteEvent | 2 229 | 1,3 % |
| PullRequestEvent | 144 | 0,1 % |
| IssueCommentEvent | 51 | < 0,1 % |
| IssuesEvent | 42 | < 0,1 % |
| PullRequestReviewEvent | 32 | < 0,1 % |
| WatchEvent | 32 | < 0,1 % |
| PullRequestReviewCommentEvent | 31 | < 0,1 % |
| ReleaseEvent | 17 | < 0,1 % |
| ForkEvent | 11 | < 0,1 % |
| CommitCommentEvent / MemberEvent / PublicEvent | 2 chacun | < 0,1 % |
| GollumEvent | 1 | < 0,1 % |

**15 types distincts observés sur cette heure.**

> ⚠️ **Anomalie majeure.** Historiquement `PushEvent` représente 40 à 50 % du
> trafic GitHub. Ici : 95,2 %. Les quatre types principaux concentrent
> 99,8 % du volume, et toute l'activité collaborative (PR, issues, reviews)
> tient dans 0,2 %.

---

## 3. Anomalie détectée — activité automatisée massive

Les repos les plus actifs de l'heure ne sont pas des projets open source :

| Repo | Événements |
|---|---|
| zerotraceh1/er-forge-probe | 294 |
| r00tsh00t12345/email-probe | 248 |
| ugmoddev/API-NEW-NAT-3- | 182 |
| Mohammad1785/seed_flowmap | 178 |
| ugmoddev/noti-api-server | 175 |
| xolirx/list-check | 164 |

Noms évocateurs (`probe`, `check`, `runner`), volumes très élevés sur une
seule heure, comptes sans historique apparent. Il s'agit très probablement
d'activité automatisée, voire d'abus de la plateforme.

**Or la détection de bots par le nom de compte ne trouve que 6,9 % du volume :**

| Acteur | Événements | Détecté `[bot]` |
|---|---|---|
| github-actions[bot] | 8 785 | ✅ |
| dependabot[bot] | 862 | ✅ |
| ugmoddev | 357 | ❌ |
| zerotraceh1 | 294 | ❌ |
| r00tsh00t12345 | 248 | ❌ |
| renovate[bot] | 203 | ✅ |
| Mohammad1785 | 178 | ❌ |
| cursor[bot] | 169 | ✅ |
| xolirx | 164 | ❌ |
| swa-runner-app[bot] | 162 | ✅ |

**Conclusion : l'heuristique par nom est insuffisante.** L'activité
automatisée ne se déclare pas. Il faut une détection comportementale
(fréquence, régularité des intervalles, concentration sur un repo unique,
âge du compte, motifs de branches).

**Décision :** ceci devient un axe central du projet et non un simple
nettoyage. Angle retenu :
*« Pipeline de qualification de l'activité GitHub — isoler le signal humain
dans un flux à 95 % automatisé. »*

---

## 4. Variabilité du payload

| Type | Nb de clés dans `payload` | Clés |
|---|---|---|
| PushEvent | 5 | before, head, push_id, ref, repository_id |
| CreateEvent | 6 | description, full_ref, master_branch, pusher_type, ref, ref_type |
| DeleteEvent | 4 | full_ref, pusher_type, ref, ref_type |
| PullRequestEvent | 7 | action, assignee, assignees, label, labels, number, pull_request |
| IssueCommentEvent | 3 | action, comment, issue |
| IssuesEvent | 6 | action, assignee, assignees, issue, label, labels |
| PullRequestReviewEvent | 3 | action, pull_request, review |
| WatchEvent | 1 | action |

**De 1 à 7 clés selon le type, sans aucun recouvrement entre familles.**

La variabilité existe aussi **au niveau racine** : le champ `org` est présent
sur certains événements (WatchEvent observé) et absent sur d'autres
(PullRequestEvent observé).

**Décision :** impossible d'aplatir en une table unique. La couche Silver
aura une table par famille d'événements, plus une table socle contenant les
champs communs (`id`, `type`, `actor`, `repo`, `created_at`).

---

## 5. Schema drift — le payload des PR est amputé

Comparaison entre la documentation courante et la réalité observée :

| Champ attendu | Présent ? |
|---|---|
| `pull_request.url` / `id` / `number` | ✅ |
| `pull_request.head` (ref, sha, repo) | ✅ |
| `pull_request.base` (ref, sha, repo) | ✅ |
| `pull_request.title` | ❌ |
| `pull_request.created_at` / `merged_at` | ❌ |
| `pull_request.additions` / `deletions` / `changed_files` | ❌ |
| `pull_request.base.repo.language` | ❌ |
| `pull_request.base.repo.stargazers_count` | ❌ |

Le champ `action` prend la valeur `"merged"` (et non `"closed"` avec un
booléen `merged`, comme le décrit l'API REST).

**Conséquences et décisions :**

1. **Analyse par langage impossible depuis les archives.** Il faudra
   enrichir via l'API REST GitHub sur les repos les plus actifs, avec
   gestion du rate limiting et cache en table de dimension à
   rafraîchissement lent.
2. **`fact_pr_lifecycle` ne peut pas lire `merged_at`.** La durée sera
   reconstruite par **sessionisation** : jointure entre l'événement
   `action='opened'` et l'événement `action='merged'` de la même PR, via
   `pull_request.id`, en s'appuyant sur le `created_at` de l'événement.
   Ces deux événements peuvent être séparés de plusieurs semaines, donc
   situés dans des partitions différentes.

---

## 6. Qualité des données

- Aucun champ obligatoire manquant sur cette heure
  (`id`, `type`, `actor`, `repo`, `created_at` tous présents).
- Aucune ligne JSON invalide détectée.

**Décision :** malgré ce résultat propre, la couche Silver mettra les lignes
illisibles en **quarantaine** dans un dossier dédié plutôt que de les
ignorer silencieusement. Un seul échantillon ne prouve pas l'absence du
problème.

---

## 7. Décisions retenues pour l'architecture

| # | Décision | Justification |
|---|---|---|
| 1 | Bronze = fichiers gzip intacts, partitionnés `year/month/day/hour` | Rejouabilité totale, 92 Go pour 6 mois |
| 2 | Bots conservés avec un drapeau, jamais supprimés | Jeter en Bronze est irréversible |
| 3 | Détection d'automatisation **comportementale**, pas par nom | Le nom ne capte que 6,9 % du phénomène |
| 4 | Silver = une table par famille d'événements + une table socle | Payload de 1 à 7 clés, aucun recouvrement |
| 5 | Lignes invalides mises en quarantaine | Ne jamais perdre de donnée en silence |
| 6 | Langage des repos obtenu par enrichissement API | Champ absent des archives |
| 7 | Durée des PR reconstruite par sessionisation | `merged_at` absent |
| 8 | Silver et Gold en Parquet / Iceberg | Gzip JSON illisible efficacement à 460 Go |

---

## 8. À vérifier avant de figer l'architecture

- [ ] Le taux de 95,2 % de `PushEvent` se confirme-t-il sur d'autres heures
      et d'autres mois ? (tester 2026-07-15 03h, 2026-06-02 12h, 2026-02-10 20h)
- [ ] Le payload des PR était-il plus riche dans le passé ? Tester une heure
      de 2023 pour dater le changement de schéma.
- [ ] Existe-t-il des heures manquantes chez GH Archive (404) ?
- [ ] Quelle proportion de repos supprimés / privatisés ?

---

## Ce que cette étape a apporté

Sans exploration préalable, trois erreurs auraient été commises :

1. Construire `fact_pr_lifecycle` sur des champs (`merged_at`, `additions`)
   qui n'existent pas — découvert en semaine 3, à refaire entièrement.
2. Concevoir une table plate unique, incompatible avec un payload variant
   de 1 à 7 clés.
3. Filtrer les bots par leur nom, en manquant l'essentiel de l'activité
   automatisée.

Une heure d'analyse a évité une réécriture complète du pipeline.