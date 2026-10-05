# Mosquitto no notebook

O broker MQTT (Eclipse Mosquitto) roda no notebook. O Milk-V Duo S **só publica** nele, e a aplicação Java do notebook assina os tópicos.

```
Milk-V Duo S  --publica-->  Mosquitto (notebook :1883)  --entrega-->  aplicação Java
```

## Conceitos

- **Broker:** o Mosquitto. Recebe as mensagens e repassa para quem assinou.
- **Tópico:** endereço em texto, separado por `/` (ex: `alpr/milkv-duo/placa`). Não precisa ser criado: passa a existir quando alguém publica nele.
- **Publicar / assinar:** quem publica envia para um tópico; quem assina recebe tudo o que chega nele a partir dali.
- **Curingas (só para assinar):** `#` pega todos os níveis abaixo (`alpr/#`); `+` pega exatamente um nível (`alpr/+/placa` recebe a placa de qualquer device).
- **QoS:** `0` = no máximo uma vez, sem confirmação; `1` = pelo menos uma vez, com confirmação do broker (é o que o pipeline usa).
- **Retida (`-r`):** o broker guarda a última mensagem do tópico e entrega na hora para quem assinar depois. Usada no tópico de status.

## Configuração

Instalação: `C:\Program Files\Mosquitto` (no PATH). Como serviço do Windows, o Mosquitto lê o `mosquitto.conf` dessa mesma pasta:

```
listener 1883 0.0.0.0
allow_anonymous true
```

- `listener 1883 0.0.0.0`: aceita conexão de qualquer interface de rede, não só do próprio notebook (o padrão do Mosquitto 2.x é só local).
- `allow_anonymous true`: sem usuário/senha. Aceitável na rede de casa; não exponha a porta fora dela.

Depois de alterar o `mosquitto.conf`, reinicie o serviço.

## Rede entre o device e o notebook

O `--mqtt-host` do pipeline é o IP do notebook **na rede que o liga ao device**, não qualquer IP dele. O notebook tem um endereço por interface, e o device só alcança o da rede em que ele também está.

Para descobrir qual usar, compare os dois lados:

```sh
# no device: interfaces e IPs dele
ip addr | grep "inet "
```

```powershell
# no notebook: interfaces e IPs dele
Get-NetIPAddress -AddressFamily IPv4 | Format-Table InterfaceAlias, IPAddress, PrefixLength
```

Use o IP do notebook que está na mesma faixa de um IP do device. Se o device responder `Network unreachable` (ex: no `ping`), o IP escolhido é de uma rede que ele não enxerga.

### Cabo USB-C (como está montado hoje)

Ligado por USB, o device cria uma rede própria com o notebook. O Windows mostra uma interface de rede extra (aqui, "Ethernet 2 / UsbNcm Host Device") como "Unidentified network", e cada lado ganha um IP nessa rede. Exemplo desta montagem:

| lado | interface | IP |
|---|---|---|
| device | `usb0` | `192.168.42.1` |
| notebook | `Ethernet 2` | `192.168.42.201` → é o `--mqtt-host` |

A faixa e os IPs dependem do device, do firmware e do driver. Confira sempre com os comandos acima. O IP do lado do notebook também pode mudar se a interface USB for recriada (ex: outra porta USB, reinstalação do driver).

O Windows classifica essa rede como **Pública**, e ela costuma não aceitar a troca para Privada. Por isso a regra do Wi-Fi não vale aqui: é preciso uma regra no perfil Público, restrita à faixa da rede USB (PowerShell como administrador, trocando a faixa pela que aparecer no seu caso):

```powershell
New-NetFirewallRule -DisplayName "Mosquitto MQTT (USB Milk-V)" -Direction Inbound -Protocol TCP -LocalPort 1883 -RemoteAddress 192.168.42.0/24 -Action Allow -Profile Public
```

O `-RemoteAddress` mantém a porta fechada para outras redes públicas (ex: Wi-Fi de um café); só a faixa do cabo é liberada.

### Wi-Fi (device e notebook no mesmo roteador)

- O `--mqtt-host` é o IP do notebook no Wi-Fi (`ipconfig`, "Endereço IPv4" do Wi-Fi). Vale reservar esse IP no roteador para ele não mudar.
- Regra de entrada `Mosquitto MQTT`, TCP 1883, perfil **Privado**. O Wi-Fi do notebook precisa estar como **Privado**, senão a regra não vale (PowerShell como administrador):

```powershell
Get-NetConnectionProfile
Set-NetConnectionProfile -InterfaceAlias "Wi-Fi" -NetworkCategory Private
```

- Alguns roteadores isolam os aparelhos entre si (isolamento de AP, comum em rede de convidados). Nesse caso o device não alcança o notebook, mesmo na mesma rede.

## Serviço

Os comandos que alteram o serviço pedem PowerShell **como administrador**.

| ação | comando |
|---|---|
| ver se está rodando | `Get-Service mosquitto` |
| iniciar | `Start-Service mosquitto` |
| parar | `Stop-Service mosquitto` |
| reiniciar (após mudar o conf) | `Restart-Service mosquitto` |
| iniciar junto com o Windows | `Set-Service mosquitto -StartupType Automatic` |
| não iniciar com o Windows | `Set-Service mosquitto -StartupType Manual` |
| registrar o serviço | `mosquitto install` (rodado de `C:\Program Files\Mosquitto`) |
| remover o serviço | `mosquitto uninstall` |

Conferir em qual endereço o broker está escutando:

```powershell
Get-NetTCPConnection -LocalPort 1883 -State Listen | Format-Table LocalAddress, LocalPort
```

`0.0.0.0` = aceita conexão pela rede. `127.0.0.1` = só local (o serviço não leu o `mosquitto.conf` acima).

### Rodar com log na tela

O serviço roda em segundo plano, sem log. Para ver cada conexão e mensagem (útil no primeiro teste com o device), pare o serviço e rode o broker em primeiro plano:

```powershell
Stop-Service mosquitto
mosquitto -c "C:\Program Files\Mosquitto\mosquitto.conf" -v
```

Ctrl+C encerra; depois `Start-Service mosquitto` volta ao normal. Só um dos dois pode ocupar a porta 1883 por vez.

## Testes

### Enviar e receber no notebook

Terminal 1, assina tudo abaixo de `alpr/` e fica esperando (`-v` mostra o tópico junto da mensagem):

```powershell
mosquitto_sub -h localhost -t "alpr/#" -v
```

Terminal 2, publica:

```powershell
mosquitto_pub -h localhost -t alpr/teste -m "ola"
```

No terminal 1 deve aparecer `alpr/teste ola`.

### Pelo IP da rede

Mesmo teste, trocando `localhost` pelo IP do notebook. Confirma que o broker aceita conexão pela rede:

```powershell
mosquitto_pub -h <ip-do-notebook> -t alpr/teste -m "via rede"
```

Isso não testa o firewall (conexão do notebook para ele mesmo não passa por ele). O firewall só é testado de verdade a partir de outro aparelho, como o device abaixo.

### A partir do Milk-V Duo S

No SSH do device (o `mosquitto_pub` já vem instalado nele), com o `mosquitto_sub` do terminal 1 aberto no notebook:

```sh
mosquitto_pub -h <ip-do-notebook> -t alpr/teste -m "do device"
```

Se aparecer `alpr/teste do device` no notebook, rede, firewall e broker estão ok. Se falhar, a mensagem indica onde está o problema:

- **`Network unreachable`:** o IP não é de uma rede que o device enxerga. Veja [Rede entre o device e o notebook](#rede-entre-o-device-e-o-notebook).
- **`Connection refused`:** chegou no notebook, mas nada aceitou. Confira se o serviço está rodando e escutando em `0.0.0.0` (seção Serviço).
- **Demora e falha por timeout:** o firewall está descartando. Confira se existe regra para o perfil da rede usada (Pública no cabo USB, Privada no Wi-Fi) e se não há regra de **bloqueio** para o `mosquitto.exe`, que vence a de permissão.

### Mensagem retida

```powershell
mosquitto_pub -h localhost -t alpr/status -m "online" -r
```

Feche e abra de novo o `mosquitto_sub`: a mensagem aparece na hora, mesmo publicada antes. Para apagar a retida de um tópico, publique uma mensagem vazia retida:

```powershell
mosquitto_pub -h localhost -t alpr/status -r -n
```

## Acompanhar o pipeline

Tópicos publicados pelo device (`<prefixo>` = `alpr/<hostname>`, ex: `alpr/milkv-duo`):

| tópico | conteúdo | QoS | retida |
|---|---|---|---|
| `<prefixo>/placa` | JSON de cada placa válida lida | 1 | não |
| `<prefixo>/status` | `online` / `offline` | 1 | sim |

```powershell
# tudo o que o pipeline publica
mosquitto_sub -h localhost -t "alpr/#" -v

# só as placas, de qualquer device, com QoS 1 (como a aplicação Java deve assinar)
mosquitto_sub -h localhost -t "alpr/+/placa" -q 1 -v

# só o status
mosquitto_sub -h localhost -t "alpr/+/status" -v
```

Com o pipeline rodando (`main.py --mqtt-host <ip-do-notebook>`), o primeiro comando mostra algo como:

```
alpr/milkv-duo/status online
alpr/milkv-duo/placa {"placa": "ABC1D23", "padrao": "mercosul", "confianca": {"placa": 0.87, "caracteres": 0.91}, "dispositivo": "milkv-duo"}
```

O formato do JSON, a regra de repetição e o comportamento sem conexão estão em [src/alpr/README.md](src/alpr/README.md#envio-mqtt).

## Opções mais usadas

| opção | em | o que faz |
|---|---|---|
| `-h <host>` | pub/sub | endereço do broker (padrão: `localhost`) |
| `-p <porta>` | pub/sub | porta do broker (padrão: `1883`) |
| `-t <tópico>` | pub/sub | tópico (no sub, pode repetir e usar `#` / `+`) |
| `-m <texto>` | pub | conteúdo da mensagem |
| `-n` | pub | mensagem vazia (com `-r`, apaga a retida) |
| `-q <0\|1\|2>` | pub/sub | QoS |
| `-r` | pub | publica como retida |
| `-v` | sub | mostra o tópico antes da mensagem |
| `-C <n>` | sub | sai depois de receber `n` mensagens |
| `-W <s>` | sub | sai depois de `s` segundos |
| `-d` | pub/sub | log de depuração do protocolo (conexão, confirmações) |
