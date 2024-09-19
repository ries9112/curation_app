import streamlit as st
import requests
import pandas as pd
import os
from datetime import datetime, timedelta
import numpy as np
from scipy.optimize import minimize

# GraphQL query to get subgraph deployment data
@st.cache_data
def get_subgraph_deployments():
    url = "https://gateway.thegraph.com/api/040d2183b97fb279ac2cb8fb2c78beae/subgraphs/id/DZz4kDTdmzWLWsV373w2bSmoar3umKKH9y82SUKr5qmp"
    query = """
    {
      subgraphDeployments(first: 1000, orderBy: signalAmount, orderDirection: desc) {
        ipfsHash
        signalAmount
        signalledTokens
      }
    }
    """
    response = requests.post(url, json={'query': query})
    deployments = response.json()['data']['subgraphDeployments']
    # Print fetched deployments for debugging
    # st.write("Fetched Deployments:", deployments)
    return deployments

# Function to process CSV files and aggregate query fees and counts
@st.cache_data
def process_csv_files(directory):
    now = datetime.now()
    week_ago = now - timedelta(days=7)

    query_fees = {}
    query_counts = {}

    for filename in os.listdir(directory):
        if filename.endswith('.csv'):
            df = pd.read_csv(os.path.join(directory, filename))
            df['end_epoch'] = pd.to_datetime(df['end_epoch'])

            # Filter data for the last week
            df_week = df[df['end_epoch'] > week_ago]

            # Group by subgraph deployment and sum query fees and counts
            grouped_fees = df_week.groupby('subgraph_deployment_ipfs_hash')['total_query_fees'].sum()
            grouped_counts = df_week.groupby('subgraph_deployment_ipfs_hash')['query_count'].sum()

            for ipfs_hash, fees in grouped_fees.items():
                if ipfs_hash in query_fees:
                    query_fees[ipfs_hash] += fees
                else:
                    query_fees[ipfs_hash] = fees

            for ipfs_hash, count in grouped_counts.items():
                if ipfs_hash in query_counts:
                    query_counts[ipfs_hash] += count
                else:
                    query_counts[ipfs_hash] = count

    return query_fees, query_counts

@st.cache_data
def get_grt_price():
    url = "https://gateway.thegraph.com/api/040d2183b97fb279ac2cb8fb2c78beae/subgraphs/id/4RTrnxLZ4H8EBdpAQTcVc7LQY9kk85WNLyVzg5iXFQCH"
    query = """
    {
      assetPairs(
        first: 1
        where: {asset: "0xc944e90c64b2c07662a292be6244bdf05cda44a7", comparedAsset: "0x0000000000000000000000000000000000000348"}
      ) {
        currentPrice
      }
    }
    """
    response = requests.post(url, json={'query': query})
    data = response.json()
    return float(data['data']['assetPairs'][0]['currentPrice'])

def calculate_opportunities(deployments, query_fees, query_counts, grt_price):
    opportunities = []

    for deployment in deployments:
        ipfs_hash = deployment['ipfsHash']
        signal_amount = float(deployment['signalAmount']) / 1e18  # Convert wei to GRT
        signalled_tokens = float(deployment['signalledTokens']) / 1e18  # Convert wei to GRT

        if ipfs_hash in query_counts:
            weekly_queries = query_counts[ipfs_hash]
            annual_queries = weekly_queries * 52  # Annualize the queries

            # Calculate total earnings based on $4 per 100,000 queries
            total_earnings = (annual_queries / 100000) * 4

            # Calculate the curator's share (10% of total earnings)
            curator_share = total_earnings * 0.1

            # Calculate the portion owned by the curator
            if signalled_tokens > 0:
                portion_owned = signal_amount / signalled_tokens
            else:
                portion_owned = 0

            # Calculate estimated annual earnings for this curator
            estimated_earnings = curator_share * portion_owned

            # Calculate APR using GRT price
            if signal_amount > 0:
                apr = (estimated_earnings / (signal_amount * grt_price)) * 100
            else:
                apr = 0

            opportunities.append({
                'ipfs_hash': ipfs_hash,
                'signal_amount': signal_amount,
                'signalled_tokens': signalled_tokens,
                'annual_queries': annual_queries,
                'total_earnings': total_earnings,
                'curator_share': curator_share,
                'estimated_earnings': estimated_earnings,
                'apr': apr,
                'weekly_queries': weekly_queries
            })

    # Filter out subgraphs with zero signal amounts
    opportunities = [opp for opp in opportunities if opp['signal_amount'] > 0]

    # Sort opportunities by APR in descending order
    return sorted(opportunities, key=lambda x: x['apr'], reverse=True)

@st.cache_data
def get_user_curation_signal(wallet_address):
    url = "https://gateway.thegraph.com/api/040d2183b97fb279ac2cb8fb2c78beae/subgraphs/id/DZz4kDTdmzWLWsV373w2bSmoar3umKKH9y82SUKr5qmp"
    query = """
    {
      nameSignals(where: {curator: "%s"}) {
        subgraph {
          currentVersion {
            subgraphDeployment {
              ipfsHash
            }
          }
        }
        signal
      }
    }
    """ % wallet_address
    response = requests.post(url, json={'query': query})
    data = response.json()['data']['nameSignals']
    return {item['subgraph']['currentVersion']['subgraphDeployment']['ipfsHash']: float(item['signal']) / 1e18 for item in data}

def calculate_user_opportunities(user_signals, opportunities, grt_price):
    user_opportunities = []
    for opp in opportunities:
        ipfs_hash = opp['ipfs_hash']
        if ipfs_hash in user_signals:
            user_signal = user_signals[ipfs_hash]
            total_signal = opp['signalled_tokens']
            portion_owned = user_signal / total_signal if total_signal > 0 else 0
            estimated_earnings = opp['curator_share'] * portion_owned
            apr = (estimated_earnings / (user_signal * grt_price)) * 100 if user_signal > 0 else 0
            
            user_opportunities.append({
                'ipfs_hash': ipfs_hash,
                'user_signal': user_signal,
                'total_signal': total_signal,
                'portion_owned': portion_owned,
                'estimated_earnings': estimated_earnings,
                'apr': apr,
                'weekly_queries': opp['weekly_queries']
            })
    
    return sorted(user_opportunities, key=lambda x: x['apr'], reverse=True)

def main():
    st.title("Curation Signal Allocation Optimizer")
    st.write("This app helps you allocate your curation signal across subgraphs to maximize your APR.")

    # User Inputs
    wallet_address = st.text_input("Enter your wallet address (optional)")
    total_signal_to_add = st.number_input("Total signal amount to add (GRT)", value=10000, min_value=0)
    num_subgraphs = st.number_input("Number of subgraphs to allocate across", value=5, min_value=1)
    min_queries = st.number_input("Minimum number of queries in the past 7 days", value=0, min_value=0)

    st.write("Calculating opportunities...")

    # Data Retrieval and Processing
    deployments = get_subgraph_deployments()
    query_fees, query_counts = process_csv_files('python_data/hourly_query_volume')
    grt_price = get_grt_price()
    opportunities = calculate_opportunities(deployments, query_fees, query_counts, grt_price)

    if wallet_address:
        user_signals = get_user_curation_signal(wallet_address)
        user_opportunities = calculate_user_opportunities(user_signals, opportunities, grt_price)
        
        st.subheader("Your Current Curation Signal")
        user_data = []
        for opp in user_opportunities:
            user_data.append({
                'IPFS Hash': opp['ipfs_hash'],
                'Your Signal (GRT)': round(opp['user_signal'], 2),
                'Total Signal (GRT)': round(opp['total_signal'], 2),
                'Portion Owned': f"{opp['portion_owned']:.2%}",
                'Estimated Annual Earnings ($)': round(opp['estimated_earnings'], 2),
                'Current APR (%)': round(opp['apr'], 2),
                'Weekly Queries': opp['weekly_queries']
            })
        
        user_df = pd.DataFrame(user_data)
        st.table(user_df)
        
        total_user_signal = sum(opp['user_signal'] for opp in user_opportunities)
        total_user_earnings = sum(opp['estimated_earnings'] for opp in user_opportunities)
        overall_user_apr = (total_user_earnings / (total_user_signal * grt_price)) * 100
        
        st.write(f"Total Curated Signal: {total_user_signal:,.2f} GRT")
        st.write(f"Total Value of Curated Signal: ${total_user_signal * grt_price:,.2f}")
        st.write(f"Estimated Annual Earnings: ${total_user_earnings:,.2f}")
        st.write(f"Overall APR: {overall_user_apr:.2f}%")
        
        st.subheader("Recommendations")
        st.write("Based on your current allocations and market opportunities, consider the following:")
        
        for i, (user_opp, market_opp) in enumerate(zip(user_opportunities, opportunities[:5]), 1):
            if user_opp['ipfs_hash'] != market_opp['ipfs_hash']:
                st.write(f"{i}. Consider moving signal from {user_opp['ipfs_hash']} (APR: {user_opp['apr']:.2f}%) to {market_opp['ipfs_hash']} (APR: {market_opp['apr']:.2f}%)")
            elif user_opp['apr'] < market_opp['apr']:
                st.write(f"{i}. Consider increasing your signal on {user_opp['ipfs_hash']} to improve APR from {user_opp['apr']:.2f}% to {market_opp['apr']:.2f}%")

    # Filter opportunities based on minimum queries
    filtered_opportunities = [opp for opp in opportunities if opp['weekly_queries'] >= min_queries]

    # Sort filtered opportunities by APR
    filtered_opportunities.sort(key=lambda x: x['apr'], reverse=True)

    # Check if there are enough opportunities
    if len(filtered_opportunities) < num_subgraphs:
        st.error(f"Only {len(filtered_opportunities)} subgraphs available for allocation after filtering.")
        num_subgraphs = min(len(filtered_opportunities), num_subgraphs)

    # Select top subgraphs based on APR
    top_opportunities = filtered_opportunities[:num_subgraphs]

    # Initialize allocation dictionary
    allocations = {opp['ipfs_hash']: 0 for opp in top_opportunities}
    remaining_signal = total_signal_to_add

    # Iterative allocation process
    while remaining_signal > 0:
        best_apr = -1
        best_opp = None

        for opp in top_opportunities:
            ipfs_hash = opp['ipfs_hash']
            signal_amount = opp['signal_amount'] + allocations[ipfs_hash]
            signalled_tokens = opp['signalled_tokens'] + allocations[ipfs_hash]
            
            # Calculate APR if we add 100 more tokens
            new_signal_amount = signal_amount + 100
            new_signalled_tokens = signalled_tokens + 100
            portion_owned = new_signal_amount / new_signalled_tokens
            estimated_earnings = opp['curator_share'] * portion_owned
            apr = (estimated_earnings / (new_signal_amount * grt_price)) * 100

            if apr > best_apr:
                best_apr = apr
                best_opp = opp

        # Allocate 100 tokens to the best opportunity
        if best_opp:
            allocations[best_opp['ipfs_hash']] += min(100, remaining_signal)
            remaining_signal -= 100
        else:
            break

    # Prepare data for display
    data = []
    total_estimated_earnings_after = 0

    for opp in top_opportunities:
        ipfs_hash = opp['ipfs_hash']
        signal_amount_before = opp['signal_amount']
        signalled_tokens_before = opp['signalled_tokens']
        annual_fees = opp['total_earnings']
        curator_share = opp['curator_share']
        weekly_queries = opp['weekly_queries']

        apr_before = opp['apr']

        allocated_amount = allocations[ipfs_hash]

        # After adding tokens
        signal_amount_after = signal_amount_before + allocated_amount
        signalled_tokens_after = signalled_tokens_before + allocated_amount
        portion_owned_after = signal_amount_after / signalled_tokens_after
        estimated_earnings_after = curator_share * portion_owned_after
        apr_after = (estimated_earnings_after / (signal_amount_after * grt_price)) * 100 if allocated_amount > 0 else None

        total_estimated_earnings_after += estimated_earnings_after

        data.append({
            'IPFS Hash': ipfs_hash,
            'Signal Before (GRT)': round(signal_amount_before, 2),
            'Signal After (GRT)': round(signal_amount_after, 2),
            'APR Before (%)': round(apr_before, 2),
            'APR After (%)': round(apr_after, 2) if apr_after is not None else '-',
            'Earnings After ($)': round(estimated_earnings_after, 2),
            'Allocated Signal (GRT)': round(allocated_amount, 2),
            'Weekly Queries': weekly_queries
        })

    # Convert data to DataFrame
    df = pd.DataFrame(data)

    # Apply color coding
    def color_apr(val):
        if val == '-':
            return 'color: gray'
        val = float(val)
        if val > 10:
            color = 'green'
        elif val < 1:
            color = 'red'
        else:
            color = 'black'
        return f'color: {color}'

    st.write(f"Allocating {total_signal_to_add} GRT across {num_subgraphs} subgraphs to maximize rewards.")
    st.write(f"Minimum queries filter: {min_queries}")

    # Display the table with styling
    styled_df = df.style.applymap(color_apr, subset=['APR After (%)'])

    st.table(styled_df)

    # Improved outputs
    st.subheader("Allocation Results")
    st.write(f"Current GRT Price: ${grt_price:.4f}")
    st.write(f"Total GRT Allocated: {total_signal_to_add:,.2f} GRT")
    st.write(f"Total Value of Allocated GRT: ${total_signal_to_add * grt_price:,.2f}")
    
    st.write("Estimated Earnings:")
    st.write(f"- Per Day: ${total_estimated_earnings_after / 365:,.2f}")
    st.write(f"- Per Week: ${total_estimated_earnings_after / 52:,.2f}")
    st.write(f"- Per Month: ${total_estimated_earnings_after / 12:,.2f}")
    st.write(f"- Per Year: ${total_estimated_earnings_after:,.2f}")
    
    overall_apr = (total_estimated_earnings_after / (total_signal_to_add * grt_price)) * 100
    st.write(f"Overall APR: {overall_apr:.2f}%")

if __name__ == "__main__":
    main()
