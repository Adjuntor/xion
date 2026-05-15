# How to run
```
git clone https://github.com/Adjuntor/xion.git
cd xion
pip install --no-cache-dir -r requirements.txt
```
Edit with the correct values the config file.
Run the bot.
```
python3 main.py
```

# Docker Image
Requires docker to be installed.
```
git clone https://github.com/Adjuntor/xion.git
cd xion
```
Edit with the correct values the config file.
```
docker build -t xion .
docker run -d --name=xion -v <volume-name>:/usr/src/app/config --restart=always xion
```

# Docker Compose
Requires docker and docker compose to be installed.
Edit the Volume location on the compose.
```
git clone https://github.com/Adjuntor/xion.git
cd shiva
```
Edit with the correct values the config file.
```
docker-compose up -d
```

# Delete Docker Container
```
docker stop xion
docker rm xion
```

# Updating
To update the bot use the command below.
```
git pull
```
