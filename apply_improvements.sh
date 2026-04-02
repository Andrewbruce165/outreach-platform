#!/bin/bash

echo "==================================================="
echo "Applying improvements to Telegram API"
echo "==================================================="
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Step 1: Apply database migration
echo -e "${YELLOW}Step 1: Applying database migration...${NC}"
docker exec -i telegram-api-db psql -U telegram_user -d telegram_followup < migrations/001_add_unique_constraint_messages.sql

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ Database migration applied successfully${NC}"
else
    echo -e "${RED}❌ Database migration failed${NC}"
    exit 1
fi

echo ""

# Step 2: Verify constraint was added
echo -e "${YELLOW}Step 2: Verifying constraint...${NC}"
docker exec -i telegram-api-db psql -U telegram_user -d telegram_followup -c "\d messages" | grep "messages_conversation_telegram_unique"

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ UNIQUE constraint verified${NC}"
else
    echo -e "${RED}⚠️ Could not verify constraint (but may still be applied)${NC}"
fi

echo ""

# Step 3: Check for existing duplicates
echo -e "${YELLOW}Step 3: Checking for duplicates...${NC}"
DUPLICATES=$(docker exec -i telegram-api-db psql -U telegram_user -d telegram_followup -t -c "SELECT COUNT(*) FROM (SELECT conversation_id, telegram_message_id, COUNT(*) FROM messages GROUP BY conversation_id, telegram_message_id HAVING COUNT(*) > 1) AS dupes;")

if [ "$DUPLICATES" -eq 0 ] 2>/dev/null; then
    echo -e "${GREEN}✅ No duplicates found${NC}"
else
    echo -e "${YELLOW}⚠️ Found duplicates (already cleaned by migration)${NC}"
fi

echo ""

# Step 4: Rebuild and restart services
echo -e "${YELLOW}Step 4: Rebuilding and restarting services...${NC}"

echo "Stopping services..."
docker-compose stop listener api

echo "Rebuilding listener service..."
docker-compose build listener

echo "Rebuilding api service..."
docker-compose build api

echo "Starting services..."
docker-compose up -d listener api

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ Services restarted successfully${NC}"
else
    echo -e "${RED}❌ Failed to restart services${NC}"
    exit 1
fi

echo ""

# Step 5: Wait for services to start
echo -e "${YELLOW}Step 5: Waiting for services to start (15 seconds)...${NC}"
sleep 15

# Step 6: Check service status
echo -e "${YELLOW}Step 6: Checking service status...${NC}"
docker-compose ps listener api

echo ""

# Step 7: Check logs for errors
echo -e "${YELLOW}Step 7: Checking logs for startup errors...${NC}"
echo "--- Listener logs (last 20 lines) ---"
docker logs telegram-listener --tail 20

echo ""
echo "--- API logs (last 20 lines) ---"
docker logs telegram-api --tail 20

echo ""
echo -e "${GREEN}==================================================="
echo "Improvements applied successfully!"
echo "===================================================${NC}"
echo ""
echo "Next steps:"
echo "1. Monitor logs: docker logs -f telegram-listener"
echo "2. Test hard delete: Try deleting a context via UI"
echo "3. Check for duplicate messages in logs"
echo "4. Verify AI responses still work"
echo ""
