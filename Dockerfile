# 🐍 Use an official Python base image
FROM python:3.10-slim

# 📦 Install system tools needed to download and unpack the chess engines
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# 📂 Set up the app workspace directory
WORKDIR /app

# 📥 Download, unpack, and configure Standard Stockfish 18
RUN wget https://github.com -O stockfish.tar \
    && tar -xf stockfish.tar --strip-components=1 \
    && mv stockfish-ubuntu-x86-64-avx2 ./stockfish \
    && rm -f stockfish.tar

# 📥 Download, unpack, and configure Fairy Stockfish 14.1
RUN wget https://github.com -O fairy.zip \
    && unzip -o fairy.zip \
    && mv fairy-stockfish-large-linux-x86-64 ./fairy-stockfish \
    && rm -f fairy.zip

# 🔑 Grant execution rights to the engines
RUN chmod +x ./stockfish ./fairy-stockfish

# 🐍 Install your Python app libraries
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 📑 Copy your bot code files into the container
COPY . .

# 🚀 Execute the application process
CMD ["python", "bot.py"]
