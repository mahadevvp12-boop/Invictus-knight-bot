FROM python:3.10-slim

# 📦 Install system tools needed to download and unpack the chess engines
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    unzip \
    tar \
    && rm -rf /var/lib/apt/lists/*

# 📂 Set up the app workspace directory
WORKDIR /app

# 📥 Download, unpack, and configure Standard Stockfish 15.1
RUN wget https://github.com/official-stockfish/Stockfish/releases/download/sf_15.1/stockfish-15.1-linux-x86-64-avx2.tar.gz -O stockfish.tar.gz \
    && tar -xzf stockfish.tar.gz \
    && mv stockfish-15.1-linux-x86-64-avx2 ./stockfish \
    && rm -f stockfish.tar.gz

# 📥 Download, unpack, and configure Fairy Stockfish 14.1
RUN wget https://github.com/fairy-stockfish/Fairy-Stockfish/releases/download/fairy_sf_14.1/fairy-stockfish-large-linux-x86-64.zip -O fairy.zip \
    && unzip -o fairy.zip \
    && mv fairy-stockfish-large-linux-x86-64 ./fairy-stockfish \
    && rm -f fairy.zip

# 🔑 Grant execution rights to the engines
RUN chmod +x ./stockfish ./fairy-stockfish

# 🐍 Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 📑 Copy bot code into the container
COPY . .

# 🚀 Execute the bot application
CMD ["python", "bot.py"]
