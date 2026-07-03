# Desplegament del solver amb Docker

Guia pas a pas per tenir l'API del solver corrent en un contenidor Docker, tant
en un **PC Windows local** (per fer proves) com en una **VM del Proxmox** (per
a les resolucions serioses). En tots dos casos, l'editor d'horaris s'hi
connecta posant la URL del servidor a l'apartat «Execució del solver» de la
vista Solver.

---

## 1. En un PC Windows local

### 1.1 Requisits (només el primer cop)

**WSL2** (el backend de Docker a Windows). En un PowerShell **d'administrador**:

```powershell
wsl --install --no-distribution
```

> `--no-distribution` instal·la només el nucli WSL2, sense cap Linux sencer,
> que és tot el que Docker necessita. Normalment no cal reiniciar, però si
> Docker es queixa de WSL en el pas següent, reinicieu i llestos.

**Docker Desktop:**

```powershell
winget install -e --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements --silent
```

Després:

1. Obriu **Docker Desktop** (menú Inici) i accepteu l'acord de servei
   (l'inici de sessió es pot ometre).
2. Espereu que la icona de la balena digui *Engine running*.
3. Obriu una **terminal nova** (les obertes abans no veuen la comanda `docker`).

### 1.2 Engegar el solver

```powershell
cd C:\Git\HorarisSolver
docker compose up -d --build
```

L'API queda a **http://localhost:8000** (documentació interactiva a
[http://localhost:8000/docs](http://localhost:8000/docs)).

Comprovació ràpida:

```powershell
curl http://localhost:8000/api/health
# {"estat":"ok","versio_api":"1.3.0","max_temps_solver":7200.0,...}
```

### 1.3 Gestió del contenidor

Des de `C:\Git\HorarisSolver`:

| Comanda | Efecte |
|---|---|
| `docker compose stop` | Atura el contenidor (no s'engegarà sol) |
| `docker compose start` | Torna a engegar-lo |
| `docker compose up -d --build` | Reconstrueix i engega (després d'un `git pull`) |
| `docker compose logs -f` | Veure els logs en viu |
| `docker compose down` | Atura i elimina el contenidor |

> El contenidor té `restart: unless-stopped`: s'engega sol quan arrenca Docker
> Desktop (que per defecte s'inicia amb Windows). Si no el voleu sempre actiu,
> `docker compose stop` o desactiveu l'autoinici de Docker Desktop a Settings.

---

## 2. En una VM del Proxmox

### 2.1 Crear la VM

- Debian o Ubuntu Server mínima.
- **CPU: tots els nuclis que pugueu** — és el que més accelera CP-SAT.
- RAM: 4 GB en sobra. Disc: 10 GB.

### 2.2 Instal·lar Docker i desplegar

Dins de la VM:

```bash
# Docker (script oficial)
curl -fsSL https://get.docker.com | sh

# El solver
git clone https://github.com/XeviSanmartin/HorarisSolver.git
cd HorarisSolver
docker compose up -d --build
```

L'API queda a **http://\<ip-de-la-vm\>:8000**. Aquesta és la URL que cal posar
a l'apartat «Execució del solver» de l'editor.

### 2.3 Engegar la VM només quan calgui

L'API no té estat: la VM pot estar apagada i engegar-se sota demanda.

- Des de la consola del node Proxmox (o per SSH): `qm start <vmid>`
- La VM triga ~30 s a estar servint; el contenidor s'engega sol
  (`restart: unless-stopped`).
- Per aturar-la: `qm shutdown <vmid>`.

### 2.4 Actualitzar el solver

```bash
cd HorarisSolver
git pull
docker compose up -d --build
```

---

## 3. Configuració (variables d'entorn)

Es toquen a `docker-compose.yml`:

| Variable | Per defecte | Descripció |
|---|---|---|
| `MAX_TEMPS_SOLVER` | `7200` | Límit dur (segons) del temps de resolució per petició. `GET /api/health` l'informa |
| `CORS_ORIGINS` | `*` | Orígens permesos per a peticions des del navegador, separats per comes. Per restringir-ho a l'editor: `"https://<projecte>.web.app,http://localhost:5173"` |

Després de canviar-les: `docker compose up -d` (recrea el contenidor).

**Important: un sol procés.** No afegiu `--workers N` a uvicorn ni repliqueu el
servei: el registre de feines asíncrones (`/api/jobs`) viu en memòria del
procés. El paral·lelisme ja el posa CP-SAT amb `opcions.num_workers` (threads).

---

## 4. Provar-ho des de l'editor d'horaris

1. Editor → botó **Solver** → apartat **Execució del solver**.
2. **Servidor**: `http://localhost:8000` (local) o `http://<ip-de-la-vm>:8000`
   (Proxmox). Botó **Comprova**: ha de dir *Solver v1.3.0 (límit 7200s)*.
3. Ajusteu **temps màxim** i **fils de cerca** (ideal: nuclis del servidor), i
   marqueu **Hores fixades** si voleu que les hores ja col·locades al període
   actiu quedin inamovibles.
4. **Llança el solver**: veureu el temps, les solucions trobades i l'objectiu
   (com més baix, millor). Podeu **aturar** en qualsevol moment conservant la
   millor solució.
5. En acabar, **Carrega la solució** la posa al període actiu de l'editor
   (es pot desfer amb Ctrl+Z).

La configuració queda desada al navegador (localStorage): cadascú pot apuntar
a un servidor diferent.

---

## 5. Resolució de problemes

| Símptoma | Causa probable / solució |
|---|---|
| `docker` no es reconeix (Windows) | Terminal oberta abans d'instal·lar Docker: obriu-ne una de nova |
| Docker Desktop es queixa de WSL | Reinicieu Windows (les funcionalitats WSL acabades d'activar de vegades ho demanen) |
| «No s'ha pogut contactar amb el servidor» a l'editor | El contenidor no corre (`docker compose ps`), la URL és incorrecta, o un tallafoc bloqueja el port 8000 |
| Error de CORS a la consola del navegador | `CORS_ORIGINS` no inclou l'origen de l'editor |
| `GET /api/jobs/{id}` retorna 404 al cap d'una estona | Les feines finalitzades s'esborren al cap d'1 hora, o el servidor s'ha reiniciat |
| El solver va lent | Doneu més nuclis a la VM i ajusteu-hi `opcions.num_workers`; fixeu hores per reduir l'espai de cerca |
